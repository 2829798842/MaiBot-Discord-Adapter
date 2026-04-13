from __future__ import annotations

"""语音功能管理器。

管理语音频道连接（固定/自动模式）、TTS 播放和 STT 识别。
"""

import asyncio
import io
import math
import subprocess
import time as _time
import wave
import numpy as np
import traceback
from typing import Any, Awaitable, Callable, Optional

import discord
from discord import VoiceClient
from discord.ext import voice_recv

from ...constants import DEFAULT_TTS_PLAYBACK_LEADING_SILENCE_MS
from .base import STTProvider, TTSProvider

VoiceConnectableChannel = discord.VoiceChannel | discord.StageChannel


class VoiceDataSink(voice_recv.AudioSink):  # type: ignore
    """语音数据接收器。

    使用 discord-ext-voice-recv 接收用户的 PCM 音频数据，
    按 user_id 缓冲，关麦时取出送入 STT。
    """

    def __init__(self, voice_manager: VoiceManager) -> None:
        """创建接收器并关联所属的语音管理器。

        Args:
            voice_manager: 用于回写与 STT 流程的 VoiceManager 实例。
        """
        super().__init__()
        self.voice_manager = voice_manager
        self.user_audio_buffers: dict[int, io.BytesIO] = {}
        self.active_speakers: set[int] = set()

    def wants_opus(self) -> bool:
        """声明本 Sink 是否需要 Opus 编码数据（此处使用 PCM）。

        Returns:
            固定为 False，表示由库提供解码后的 PCM。
        """
        return False

    def write(self, user: Any, data: Any) -> None:
        """接收一帧用户音频并写入对应用户的缓冲，同时通知 VAD 处理。

        Args:
            user: 说话用户对象或用户 ID（兼容两种形式）。
            data: 含 pcm 属性的数据包，或库约定的音频数据对象。
        """
        user_id = user.id if hasattr(user, "id") else user
        if user_id not in self.user_audio_buffers:
            self.user_audio_buffers[user_id] = io.BytesIO()
        pcm_bytes: bytes = b""
        if hasattr(data, "pcm"):
            pcm_bytes = data.pcm
            self.user_audio_buffers[user_id].write(pcm_bytes)
        self.active_speakers.add(user_id)
        if pcm_bytes:
            self.voice_manager._on_vad_frame(user, user_id, pcm_bytes)

    def cleanup(self) -> None:
        """关闭所有用户缓冲并清空状态，在停止监听时调用。

        Returns:
            None
        """
        for buffer in self.user_audio_buffers.values():
            buffer.close()
        self.user_audio_buffers.clear()
        self.active_speakers.clear()

    async def get_audio_data(self, user_id: int) -> bytes:
        """读取指定用户缓冲中的 PCM 数据并清空该缓冲（供关麦后 STT 使用）。

        Args:
            user_id: Discord 用户 ID。

        Returns:
            该用户自上次清空以来累积的音频字节；无缓冲时返回空 bytes。
        """
        buffer = self.user_audio_buffers.get(user_id)
        if buffer:
            data = buffer.getvalue()
            buffer.seek(0)
            buffer.truncate()
            return data
        return b""


def convert_audio_to_pcm(audio_data: bytes, logger: Any) -> bytes:
    """将 Discord 侧 PCM（48kHz 立体声 s16le）转为 16kHz 单声道 PCM，供 STT 使用。

    通过 FFmpeg 管道转换；若 FFmpeg 不可用或转换失败，则记录日志并原样返回输入。

    Args:
        audio_data: 原始 PCM 字节流。
        logger: 用于输出错误与诊断的日志器。

    Returns:
        转换后的 PCM 字节；失败时返回未修改的 audio_data。
    """
    try:
        process = subprocess.Popen(
            [
                "ffmpeg",
                "-f", "s16le", "-ar", "48000", "-ac", "2",
                "-i", "pipe:0",
                "-f", "s16le", "-ar", "16000", "-ac", "1",
                "pipe:1",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        pcm_data, stderr = process.communicate(input=audio_data)
        if process.returncode != 0:
            logger.error(f"FFmpeg 转换失败: {stderr.decode()}")
            return audio_data
        return pcm_data
    except FileNotFoundError:
        logger.error("FFmpeg 未安装或不在 PATH 中")
        return audio_data
    except Exception as exc:
        logger.error(f"音频转换错误: {exc}")
        return audio_data


def _frame_db(pcm_bytes: bytes) -> float:
    """计算一帧 PCM 音频的 RMS 分贝值。

    Args:
        pcm_bytes: 16-bit signed little-endian PCM 字节流。

    Returns:
        RMS 分贝值（参考幅度 32768），静音或空帧返回 -100.0。
    """

    samples = np.frombuffer(pcm_bytes, dtype=np.int16)
    if samples.size == 0:
        return -100.0
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    if rms <= 0:
        return -100.0
    return 20.0 * math.log10(rms / 32768.0)


def _detect_audio_format(data: bytes) -> str:
    """根据魔数判断音频容器/格式，供 FFmpeg 选择解码方式。

    Args:
        data: 音频文件或流的头部字节（至少需包含可识别的文件头）。

    Returns:
        与 FFmpeg ``-f`` 兼容的格式名：``wav``、``mp3``、``flac`` 或 ``s16le``（默认裸 PCM）。
    """
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WAVE":
        return "wav"
    if data[:3] == b"ID3" or (len(data) >= 2 and data[:2] == b"\xff\xfb"):
        return "mp3"
    if data[:4] == b"fLaC":
        return "flac"
    return "s16le"


def _prepend_leading_silence(
    data: bytes, fmt: str, silence_ms: int, logger: Any
) -> bytes:
    """Prepend a short silence buffer to reduce Discord-side playback warm-up truncation."""

    if silence_ms <= 0 or not data:
        return data

    if fmt != "wav":
        logger.debug(
            "Skipping TTS leading silence padding because the payload is not WAV "
            f"[format={fmt}, silence_ms={silence_ms}]"
        )
        return data

    try:
        with wave.open(io.BytesIO(data), "rb") as reader:
            channels = reader.getnchannels()
            sample_width = reader.getsampwidth()
            frame_rate = reader.getframerate()
            frame_count = reader.getnframes()
            frames = reader.readframes(frame_count)

        if channels <= 0 or sample_width <= 0 or frame_rate <= 0:
            logger.warning(
                "Skipping TTS leading silence padding because the WAV header is invalid "
                f"[channels={channels}, sample_width={sample_width}, frame_rate={frame_rate}]"
            )
            return data

        silent_frame_count = max(1, round(frame_rate * (silence_ms / 1000.0)))
        silence = b"\x00" * silent_frame_count * channels * sample_width
        output = io.BytesIO()
        with wave.open(output, "wb") as writer:
            writer.setnchannels(channels)
            writer.setsampwidth(sample_width)
            writer.setframerate(frame_rate)
            writer.writeframes(silence)
            writer.writeframes(frames)
        return output.getvalue()
    except wave.Error as exc:
        logger.warning(
            "Failed to prepend leading silence to WAV payload; playback will continue without padding "
            f"[silence_ms={silence_ms}, error={exc}]"
        )
        return data
    except Exception as exc:
        logger.warning(
            "Unexpected error while prepending leading silence to WAV payload; playback will continue without padding "
            f"[silence_ms={silence_ms}, error={exc}]"
        )
        return data


class VoiceManager:
    """语音功能管理器。

    支持两种频道模式：
    - fixed: 指定一个频道 ID，启动后常驻。
    - auto: 指定候选频道列表，有人进入时加入，无人超时后退出。
    """

    def __init__(
        self,
        *,
        bot: discord.Client,
        logger: Any,
        voice_mode: str = "auto",
        fixed_channel_id: str = "",
        auto_channel_list: list[str] | None = None,
        idle_timeout_sec: int = 300,
        tts_provider: Optional[TTSProvider] = None,
        stt_provider: Optional[STTProvider] = None,
        on_stt_result: Optional[Callable[..., Awaitable[None]]] = None,
        enable_vad: bool = True,
        vad_threshold_db: float = -50.0,
        vad_deactivation_delay_ms: int = 500,
    ) -> None:
        """初始化语音管理器：模式、频道、超时、TTS/STT、VAD 与回调。

        Args:
            bot: Discord 客户端实例。
            logger: 日志器。
            voice_mode: ``"fixed"`` 常驻指定频道，``"auto"`` 按候选列表与空闲超时进出。
            fixed_channel_id: 固定模式下的频道 ID 字符串。
            auto_channel_list: 自动模式下可加入的候选频道 ID 列表。
            idle_timeout_sec: 自动模式下频道内无人后的断开前等待秒数。
            tts_provider: 文本转语音提供商，可为 None 表示仅连接不播报。
            stt_provider: 语音转文本提供商；为 None 时使用普通语音连接（不监听用户音频）。
            on_stt_result: STT 有结果时的异步回调 ``(member, text) -> None``。
            enable_vad: 是否启用基于音量阈值的 VAD（默认 True）。
            vad_threshold_db: VAD 分贝阈值，高于此值视为说话（默认 -50.0）。
            vad_deactivation_delay_ms: 低于阈值后保持 talking 状态的延迟毫秒数（默认 500）。
        """
        self.bot = bot
        self._logger = logger
        self._voice_mode = voice_mode
        self._fixed_channel_id = fixed_channel_id
        self._auto_channel_list: list[int] = []
        for channel_id in auto_channel_list or []:
            normalized = str(channel_id).strip()
            if not normalized:
                continue
            try:
                self._auto_channel_list.append(int(normalized))
            except ValueError:
                self._logger.warning(f"忽略无效的自动语音频道 ID: {channel_id!r}")
        self._idle_timeout_sec = idle_timeout_sec
        self.tts_provider = tts_provider
        self.stt_provider = stt_provider
        self._stt_callback = on_stt_result

        self.voice_client: Optional[VoiceClient] = None
        self._current_channel_id: Optional[int] = None
        self._running = False
        self._idle_timer_task: Optional[asyncio.Task[None]] = None

        self._voice_sink: Optional[VoiceDataSink] = None

        self._enable_vad = enable_vad
        self._vad_threshold_db = vad_threshold_db
        self._vad_deactivation_delay_s = vad_deactivation_delay_ms / 1000.0
        self._vad_user_talking: dict[int, bool] = {}
        self._vad_last_above: dict[int, float] = {}
        self._vad_deactivation_tasks: dict[int, asyncio.Task[None]] = {}
        self._vad_user_objects: dict[int, Any] = {}

        self._logger.info(
            f"语音管理器已初始化 [模式: {voice_mode}, "
            f"VAD: {'ON' if enable_vad else 'OFF'} ({vad_threshold_db} dB / {vad_deactivation_delay_ms} ms), "
            f"TTS: {type(tts_provider).__name__ if tts_provider else 'None'}, "
            f"STT: {type(stt_provider).__name__ if stt_provider else 'None'}]"
        )


    async def start(self) -> None:
        """启动管理器：固定模式则立即连接频道；自动模式仅打标并记录候选频道。

        Returns:
            None
        """
        self._running = True

        if self._voice_mode == "fixed":
            if not self._fixed_channel_id:
                self._logger.warning("固定模式但未配置频道 ID")
                return
            try:
                channel_id = int(str(self._fixed_channel_id).strip())
            except ValueError:
                self._logger.error(f"固定模式语音频道 ID 无效: {self._fixed_channel_id!r}")
                return

            if await self.connect(channel_id):
                self._logger.info(f"固定模式：已加入频道 {channel_id}")
            else:
                self._logger.error(f"固定模式：加入频道失败 {channel_id}")
        else:
            self._logger.info(
                f"自动模式：监听 {len(self._auto_channel_list)} 个候选频道"
            )

    async def stop(self) -> None:
        """停止运行、取消空闲计时并断开当前语音连接。

        Returns:
            None
        """
        self._running = False
        self._cancel_idle_timer()
        await self.disconnect()
        self._logger.info("语音管理器已停止")

    async def close(self) -> None:
        """完全关闭：先 ``stop``，再关闭 TTS/STT 提供商。

        Returns:
            None
        """
        await self.stop()
        if self.tts_provider:
            await self.tts_provider.close()
        if self.stt_provider:
            await self.stt_provider.close()
        self._logger.info("语音管理器已关闭")


    async def _resolve_voice_channel(
        self, channel_id: int
    ) -> Optional[VoiceConnectableChannel]:
        """解析语音频道，优先使用缓存，失败时回退到 REST 拉取。"""
        channel = self.bot.get_channel(channel_id)
        source = "cache"

        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
                source = "fetch"
            except discord.NotFound:
                self._logger.error(f"语音频道不存在: channel_id={channel_id}")
                return None
            except discord.Forbidden as exc:
                self._logger.error(
                    f"无权获取语音频道: channel_id={channel_id}, error={exc}"
                )
                return None
            except discord.HTTPException as exc:
                self._logger.error(
                    f"获取语音频道失败: channel_id={channel_id}, error={exc}"
                )
                return None

        if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            self._logger.error(
                "目标频道不是可连接语音频道: "
                f"channel_id={channel_id}, type={type(channel).__name__}"
            )
            return None

        guild_name = getattr(channel.guild, "name", "unknown-guild")
        self._logger.debug(
            "语音频道解析成功: "
            f"channel_id={channel.id}, guild={guild_name}, "
            f"channel={channel.name}, type={type(channel).__name__}, source={source}"
        )
        return channel

    def _describe_channel_permissions(self, channel: VoiceConnectableChannel) -> str:
        """返回机器人在目标语音频道上的关键权限快照。"""
        permissions = self._get_channel_permissions(channel)
        if permissions is None:
            return "member=unresolved"

        return (
            f"view_channel={permissions.view_channel}, "
            f"connect={permissions.connect}, "
            f"speak={permissions.speak}, "
            f"use_voice_activation={permissions.use_voice_activation}"
        )

    def _get_channel_permissions(
        self, channel: VoiceConnectableChannel
    ) -> Optional[discord.Permissions]:
        """获取机器人在目标频道上的权限对象。"""
        if self.bot.user is None:
            return None

        member = channel.guild.get_member(self.bot.user.id)
        if member is None:
            return None

        return channel.permissions_for(member)

    async def connect(self, channel_id: int) -> bool:
        """连接指定语音频道；若已连接其他频道则先断开。有 STT 时使用 VoiceRecv 客户端并启动监听。

        Args:
            channel_id: 目标语音频道 ID。

        Returns:
            连接成功为 True；频道无效或连接异常为 False。
        """
        channel: Optional[VoiceConnectableChannel] = None
        try:
            if self.bot is None:
                self._logger.error("Discord 客户端尚未绑定，无法连接语音频道")
                return False

            if (
                self.voice_client
                and self.voice_client.is_connected()
                and self._current_channel_id == channel_id
            ):
                self._logger.debug(f"已在目标语音频道中，无需重复连接: {channel_id}")
                return True

            channel = await self._resolve_voice_channel(channel_id)
            if channel is None:
                return False

            permissions = self._get_channel_permissions(channel)
            permissions_snapshot = self._describe_channel_permissions(channel)
            if permissions is not None and not permissions.view_channel:
                self._logger.error(
                    f"机器人没有查看语音频道权限: channel_id={channel_id}, permissions=({permissions_snapshot})"
                )
                return False
            if permissions is not None and not permissions.connect:
                self._logger.error(
                    f"机器人没有连接语音频道权限: channel_id={channel_id}, permissions=({permissions_snapshot})"
                )
                return False
            if permissions is not None and self.tts_provider and not permissions.speak:
                self._logger.warning(
                    f"机器人没有语音发言权限，TTS 可能无法正常播报: channel_id={channel_id}, permissions=({permissions_snapshot})"
                )

            if self.voice_client and self.voice_client.is_connected():
                self._logger.debug(
                    "切换语音频道前断开旧连接: "
                    f"from_channel_id={self._current_channel_id}, to_channel_id={channel_id}"
                )
                await self.voice_client.disconnect()
                self.voice_client = None
                self._current_channel_id = None

            if self.stt_provider:
                self.voice_client = await channel.connect(cls=voice_recv.VoiceRecvClient)
                await self._start_voice_receiving()
            else:
                self.voice_client = await channel.connect()

            self._current_channel_id = channel_id
            self._logger.info(
                "已连接到语音频道: "
                f"guild={channel.guild.name}, channel={channel.name}, "
                f"channel_id={channel_id}, type={type(channel).__name__}, "
                f"permissions=({permissions_snapshot})"
            )
            return True

        except (discord.DiscordException, RuntimeError, OSError) as exc:
            channel_name = getattr(channel, "name", "unknown")
            channel_type = type(channel).__name__ if channel is not None else "unresolved"
            self._logger.error(
                "连接语音频道失败: "
                f"channel_id={channel_id}, channel={channel_name}, "
                f"type={channel_type}, error={exc}"
            )
            self._logger.debug(traceback.format_exc())
            return False

    async def disconnect(self) -> None:
        """停止播放、停止语音接收并断开与当前频道的连接。

        Returns:
            None
        """
        if self.voice_client and self.voice_client.is_playing():
            try:
                self.voice_client.stop_playing()
                await asyncio.sleep(0.1)
            except Exception:
                pass

        await self._stop_voice_receiving()

        if self.voice_client and self.voice_client.is_connected():
            try:
                await self.voice_client.disconnect()
            except Exception as exc:
                self._logger.error(f"断开语音连接失败: {exc}")
            finally:
                self.voice_client = None
                self._current_channel_id = None

    def is_connected(self) -> bool:
        """是否已建立并保持语音连接。

        Returns:
            已连接为 True，否则为 False。
        """
        return self.voice_client is not None and self.voice_client.is_connected()

    def get_connected_channel_id(self) -> Optional[int]:
        """返回当前已连接语音频道的 ID（未连接时为 None）。

        Returns:
            当前频道 ID，未连接则为 None。
        """
        return self._current_channel_id if self.is_connected() else None

    def _get_tts_provider_last_error(self) -> str:
        """Best-effort readback of the provider's last detailed synthesis error."""
        provider = self.tts_provider
        if provider is None:
            return ""

        getter = getattr(provider, "get_last_error", None)
        if callable(getter):
            try:
                return str(getter() or "").strip()
            except Exception:
                return ""

        return str(getattr(provider, "_last_error", "") or "").strip()


    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """处理成员语音状态变化：自动模式进出频道与空闲计时；检测关麦后触发 STT。

        Args:
            member: 状态发生变化的成员。
            before: 变化前的语音状态。
            after: 变化后的语音状态。

        Returns:
            None
        """
        if not self._running or member.bot:
            return

        if self._voice_mode == "auto":
            await self._handle_auto_mode_state_change(member, before, after)

        # STT: 检测麦克风开关
        was_muted = before.self_mute or before.mute
        is_muted = after.self_mute or after.mute

        if not was_muted and is_muted and before.channel:
            if self._voice_sink and self.stt_provider:
                await self._process_user_audio(member)

    async def _handle_auto_mode_state_change(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """自动模式：有人进入候选频道则连接或取消空闲计时；频道无人则启动空闲断开计时。"""
        joined_channel = after.channel
        left_channel = before.channel

        if joined_channel and joined_channel.id in self._auto_channel_list:
            if not self.is_connected():
                human_count = sum(1 for m in joined_channel.members if not m.bot)
                if human_count > 0:
                    self._cancel_idle_timer()
                    await self.connect(joined_channel.id)
            elif self._current_channel_id == joined_channel.id:
                self._cancel_idle_timer()

        if left_channel and self._current_channel_id == left_channel.id:
            if isinstance(left_channel, (discord.VoiceChannel, discord.StageChannel)):
                channel = left_channel
                human_count = sum(1 for m in channel.members if not m.bot)
                if human_count == 0:
                    self._start_idle_timer()

    def _start_idle_timer(self) -> None:
        """在取消旧任务后启动空闲超时断开任务。"""
        self._cancel_idle_timer()
        self._idle_timer_task = asyncio.create_task(self._idle_disconnect())

    def _cancel_idle_timer(self) -> None:
        """若存在未完成的空闲计时任务则取消并清空引用。"""
        if self._idle_timer_task and not self._idle_timer_task.done():
            self._idle_timer_task.cancel()
            self._idle_timer_task = None

    async def _idle_disconnect(self) -> None:
        """等待空闲超时秒数后，若频道仍无真人成员则断开连接。"""
        try:
            self._logger.info(
                f"频道内无人，{self._idle_timeout_sec} 秒后自动退出"
            )
            await asyncio.sleep(self._idle_timeout_sec)

            if self._current_channel_id:
                channel = await self._resolve_voice_channel(self._current_channel_id)
                if channel is not None:
                    human_count = sum(1 for m in channel.members if not m.bot)
                    if human_count > 0:
                        self._logger.info("超时前有人加入，取消退出")
                        return

            self._logger.info("空闲超时，自动退出语音频道")
            await self.disconnect()
        except asyncio.CancelledError:
            pass


    async def speak(self, text: str, channel_id: Optional[int] = None) -> bool:
        """使用 TTS 合成文本并在当前或指定语音频道播放。

        Args:
            text: 要播报的文本。
            channel_id: 若指定且与当前连接不一致，则先尝试切换到该频道。

        Returns:
            播放已启动为 True；无 TTS、连接失败或合成为空为 False。
        """
        if not self.tts_provider:
            self._logger.error("TTS 播放请求被忽略：未配置 TTS provider")
            return False

        try:
            provider_name = type(self.tts_provider).__name__
            requested_channel_id = channel_id or self.get_connected_channel_id()
            self._logger.info(
                "Preparing TTS playback "
                f"[provider={provider_name}, requested_channel_id={requested_channel_id}, "
                f"current_channel_id={self._current_channel_id}, chars={len(text)}]"
            )
            if channel_id:
                current = self.get_connected_channel_id()
                if current != channel_id:
                    self._logger.info(
                        "Switching voice channel before TTS playback "
                        f"[from_channel_id={current}, to_channel_id={channel_id}]"
                    )
                    if not await self.connect(channel_id):
                        self._logger.error(
                            "TTS playback aborted because the target voice channel could not be connected "
                            f"[target_channel_id={channel_id}]"
                        )
                        return False

            if not self.voice_client or not self.voice_client.is_connected():
                self._logger.error(
                    "TTS playback aborted because no active voice connection is available "
                    f"[requested_channel_id={requested_channel_id}, current_channel_id={self._current_channel_id}]"
                )
                return False

            audio_stream = await self.tts_provider.synthesize(text)
            if not audio_stream:
                provider_last_error = self._get_tts_provider_last_error()
                self._logger.error(
                    "TTS synthesis returned no audio "
                    f"[provider={provider_name}, requested_channel_id={requested_channel_id}, "
                    f"provider_error={provider_last_error or '<empty>'}]"
                )
                return False

            audio_stream.seek(0)
            raw_data = audio_stream.read()
            if not raw_data:
                self._logger.error(
                    "TTS synthesis produced an empty audio stream "
                    f"[provider={provider_name}, requested_channel_id={requested_channel_id}]"
                )
                return False
            fmt = _detect_audio_format(raw_data)
            self._logger.debug(
                "TTS audio payload ready for playback "
                f"[provider={provider_name}, requested_channel_id={requested_channel_id}, "
                f"bytes={len(raw_data)}, format={fmt}]"
            )
            padded_raw_data = _prepend_leading_silence(
                raw_data,
                fmt,
                DEFAULT_TTS_PLAYBACK_LEADING_SILENCE_MS,
                self._logger,
            )
            if len(padded_raw_data) != len(raw_data):
                self._logger.debug(
                    "Applied leading silence to TTS payload "
                    f"[provider={provider_name}, requested_channel_id={requested_channel_id}, "
                    f"format={fmt}, silence_ms={DEFAULT_TTS_PLAYBACK_LEADING_SILENCE_MS}, "
                    f"original_bytes={len(raw_data)}, padded_bytes={len(padded_raw_data)}]"
                )
                raw_data = padded_raw_data

            if fmt == "s16le":
                before_options = "-f s16le -ar 48000 -ac 1"
            else:
                before_options = ""

            audio_source = discord.FFmpegPCMAudio(
                io.BytesIO(raw_data),
                pipe=True,
                before_options=before_options,
                options="-loglevel error",
            )

            if self.voice_client.is_playing():
                self._logger.debug("Stopping currently playing audio before starting a new TTS payload")
                self.voice_client.stop_playing()

            def _after_playback(error: Optional[Exception]) -> None:
                if error is not None:
                    self._logger.error(
                        "Discord voice playback reported an asynchronous error "
                        f"[provider={provider_name}, requested_channel_id={requested_channel_id}, error={error}]"
                    )
                else:
                    self._logger.debug(
                        "Discord voice playback finished "
                        f"[provider={provider_name}, requested_channel_id={requested_channel_id}]"
                    )

            self.voice_client.play(audio_source, after=_after_playback)
            self._logger.info(
                "TTS playback started "
                f"[provider={provider_name}, requested_channel_id={requested_channel_id}, "
                f"text_preview={text[:50]!r}]"
            )
            return True

        except Exception as exc:
            provider_last_error = self._get_tts_provider_last_error()
            self._logger.error(
                "TTS playback failed "
                f"[provider={type(self.tts_provider).__name__}, requested_channel_id={channel_id or self._current_channel_id}, "
                f"provider_error={provider_last_error or '<empty>'}, error={exc}]"
            )
            self._logger.debug(traceback.format_exc())
            return False


    async def _start_voice_receiving(self) -> None:
        """在 VoiceRecv 客户端上注册 VoiceDataSink 并开始监听用户音频。"""
        if not self.voice_client or not isinstance(
            self.voice_client, voice_recv.VoiceRecvClient
        ):
            return
        try:
            self._voice_sink = VoiceDataSink(self)
            self.voice_client.listen(self._voice_sink)
            self._logger.info("语音接收已启动")
        except Exception as exc:
            self._logger.error(f"启动语音接收失败: {exc}")

    async def _stop_voice_receiving(self) -> None:
        """停止 listen 并清理 Sink 缓冲。"""
        if not self.voice_client or not isinstance(
            self.voice_client, voice_recv.VoiceRecvClient
        ):
            return
        try:
            self.voice_client.stop_listening()
            if self._voice_sink:
                self._voice_sink.cleanup()
                self._voice_sink = None
        except Exception as exc:
            self._logger.error(f"停止语音接收失败: {exc}")

    async def _process_user_audio(self, member: discord.Member) -> None:
        """取该用户缓冲音频，转 PCM 后 STT，若有文本则调用回调。

        Args:
            member: 刚关麦的用户。
        """
        if not self._voice_sink or not self.stt_provider:
            return
        try:
            audio_data = await self._voice_sink.get_audio_data(member.id)
            if not audio_data:
                return

            pcm_data = convert_audio_to_pcm(audio_data, self._logger)
            text = await self.stt_provider.recognize(pcm_data)

            if text and self._stt_callback:
                await self._stt_callback(member, text)

        except Exception as exc:
            self._logger.error(f"处理音频数据失败: {exc}")
            self._logger.debug(traceback.format_exc())

    def set_stt_callback(self, callback: Callable[..., Awaitable[None]]) -> None:
        """设置或替换 STT 结果异步回调。

        Args:
            callback: 与构造时 ``on_stt_result`` 相同签名的可等待回调。
        """
        self._stt_callback = callback


    def _on_vad_frame(self, user: Any, user_id: int, pcm_bytes: bytes) -> None:
        """每帧 PCM 写入时由 VoiceDataSink 调用，执行 VAD 判定。

        状态机逻辑（参照 SVC / Simple Voice Chat）：
        - PCM -> RMS -> dB -> 与阈值比较
        - 超过阈值 -> 标记 talking = True，记录最后活跃时间
        - 低于阈值 -> 启动 deactivation delay 计时器
        - 计时器到期仍低于阈值 -> talking = False -> 触发 STT

        Args:
            user: discord 用户对象。
            user_id: 用户 ID。
            pcm_bytes: 本帧 PCM 数据。
        """
        if not self._enable_vad or not self.stt_provider:
            return

        self._vad_user_objects[user_id] = user
        db = _frame_db(pcm_bytes)
        now = _time.monotonic()

        if db > self._vad_threshold_db:
            self._vad_last_above[user_id] = now
            if not self._vad_user_talking.get(user_id, False):
                self._vad_user_talking[user_id] = True
                self._logger.debug(f"VAD: 用户 {user_id} 开始说话 ({db:.1f} dB)")
            if user_id in self._vad_deactivation_tasks:
                task = self._vad_deactivation_tasks.pop(user_id)
                if not task.done():
                    task.cancel()
        else:
            if self._vad_user_talking.get(user_id, False):
                if user_id not in self._vad_deactivation_tasks or self._vad_deactivation_tasks[user_id].done():
                    self._vad_deactivation_tasks[user_id] = asyncio.create_task(
                        self._vad_deactivation_timer(user_id)
                    )

    async def _vad_deactivation_timer(self, user_id: int) -> None:
        """VAD 关闭延迟计时器，超时后标记停止说话并触发 STT。

        Args:
            user_id: 用户 ID。
        """
        try:
            await asyncio.sleep(self._vad_deactivation_delay_s)

            now = _time.monotonic()
            last = self._vad_last_above.get(user_id, 0)
            if now - last < self._vad_deactivation_delay_s:
                return

            self._vad_user_talking[user_id] = False
            self._logger.debug(f"VAD: 用户 {user_id} 停止说话（延迟到期）")

            user_obj = self._vad_user_objects.get(user_id)
            if user_obj and hasattr(user_obj, "id") and self._voice_sink and self.stt_provider:
                member = None
                if self.voice_client and self.voice_client.channel:
                    guild = self.voice_client.channel.guild
                    member = guild.get_member(user_id)
                if member:
                    await self._process_user_audio(member)

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._logger.error(f"VAD deactivation timer 异常: {exc}")
        finally:
            self._vad_deactivation_tasks.pop(user_id, None)
