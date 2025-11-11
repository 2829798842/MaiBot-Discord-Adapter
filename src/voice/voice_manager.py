"""模块名称: voice_manager
主要功能: 管理语音频道连接、TTS播放和STT识别
"""

from typing import Optional
import asyncio
import io
import subprocess
import traceback
import discord
from discord import VoiceClient
from discord.ext import voice_recv

from .base import TTSProvider, STTProvider
from ..logger import logger as project_logger
from ..config.voice_config import VoiceConfig

logger = project_logger.bind(name="VoiceManager")


class VoiceDataSink(voice_recv.AudioSink):  # type: ignore
    """语音数据接收器 - 接收Discord语音频道的音频数据
    
    使用 discord-ext-voice-recv 扩展来接收用户的语音数据。
    可以识别谁在说话,并将音频数据传递给 STT 进行识别。
    
    Attributes:
        voice_manager (VoiceManager): 语音管理器实例
        user_audio_buffers (dict[int, io.BytesIO]): 用户ID到音频缓冲区的映射
        active_speakers (set[int]): 当前正在说话的用户ID集合
    """

    def __init__(self, voice_manager):
        """初始化语音数据接收器
        
        Args:
            voice_manager: VoiceManager 实例
        """
        super().__init__()
        self.voice_manager = voice_manager
        self.user_audio_buffers: dict[int, io.BytesIO] = {}
        self.active_speakers: set[int] = set()

    def wants_opus(self) -> bool:
        """指示是否需要 Opus 编码的音频数据
        
        Returns:
            bool: False 表示需要解码后的 PCM 数据
        """
        return False  # 我们需要 PCM 数据用于 STT

    def write(self, user, data):
        """接收语音数据 (discord-ext-voice-recv 回调)
        
        Args:
            user: 说话的用户对象
            data: VoiceData 对象,包含音频数据
        """
        user_id = user.id if hasattr(user, 'id') else user

        # 初始化用户缓冲区
        if user_id not in self.user_audio_buffers:
            self.user_audio_buffers[user_id] = io.BytesIO()
            logger.debug(f"开始接收用户 {user_id} 的语音数据")

        # 写入PCM音频数据
        if hasattr(data, 'pcm'):
            self.user_audio_buffers[user_id].write(data.pcm)

        # 标记为活跃说话者
        self.active_speakers.add(user_id)

    def cleanup(self):
        """清理所有音频缓冲区"""
        for buffer in self.user_audio_buffers.values():
            buffer.close()
        self.user_audio_buffers.clear()
        self.active_speakers.clear()

    async def get_audio_data(self, user_id: int) -> bytes:
        """获取指定用户的音频数据
        
        Args:
            user_id: 用户ID
            
        Returns:
            bytes: 音频数据
        """
        buffer = self.user_audio_buffers.get(user_id)
        if buffer:
            data = buffer.getvalue()
            # 清空缓冲区以便下次录音
            buffer.seek(0)
            buffer.truncate()
            return data
        return b''


def convert_audio_to_pcm(audio_data: bytes) -> bytes:
    """将 Discord 音频转换为 16kHz PCM 格式 (用于 STT)

    使用 FFmpeg 将音频转换为 STT 提供商所需的格式。
    Discord 输出的音频格式: PCM 48kHz 16-bit Stereo (s16le)

    Args:
        audio_data: Discord 录制的音频数据 (PCM s16le 48kHz stereo)

    Returns:
        bytes: 转换后的 PCM 数据 (16kHz, 16bit, Mono),转换失败时返回原始数据
    """
    try:
        # 使用 FFmpeg 转换音频格式
        # 输入: PCM s16le 48kHz stereo (Discord 标准格式)
        # 输出: PCM s16le 16kHz mono (STT 标准格式)
        process = subprocess.Popen(
            [
                'ffmpeg',
                '-f', 's16le',       # 输入格式: PCM 16-bit little-endian
                '-ar', '48000',      # 输入采样率: 48kHz (Discord 标准)
                '-ac', '2',          # 输入声道: 立体声
                '-i', 'pipe:0',      # 从标准输入读取
                '-f', 's16le',       # 输出格式: PCM 16-bit
                '-ar', '16000',      # 输出采样率: 16kHz (STT 标准)
                '-ac', '1',          # 输出声道: 单声道
                'pipe:1'             # 输出到标准输出
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        pcm_data, stderr = process.communicate(input=audio_data)

        if process.returncode != 0:
            logger.error(f"FFmpeg 转换失败: {stderr.decode()}")
            return audio_data  # 返回原始数据

        logger.debug(f"音频转换成功: {len(audio_data)} bytes -> {len(pcm_data)} bytes")
        return pcm_data

    except FileNotFoundError:
        logger.error("FFmpeg 未安装或不在 PATH 中,无法转换音频格式")
        return audio_data

    except Exception as e:  # pylint: disable=broad-except
        logger.error(f"音频转换错误: {e}")
        return audio_data


class VoiceManager:
    """语音功能管理器

    负责管理语音频道连接、TTS播放和STT识别功能。


        - 管理语音频道连接（单频道固定，多频道自动切换）
        - TTS 播放（MaiBot 消息 -> 语音）
        - STT 识别（麦克风开启/关闭触发录音识别）
        - 消息格式转换（由 message_handler 和 send_handler 负责）
        - 与 MaiBot 通信（由 router 负责）

    Attributes:
        bot (discord.Client): Discord Bot 实例
        config (VoiceConfig): 语音配置对象
        tts_provider (Optional[TTSProvider]): TTS 提供商
        stt_provider (Optional[STTProvider]): STT 提供商
        enabled (bool): 语音功能是否启用
        voice_channel_whitelist (list[int]): 语音频道白名单
        check_interval (int): 频道检查间隔（秒）
        voice_client (Optional[VoiceClient]): Discord 语音客户端
        _check_task (Optional[asyncio.Task]): 后台检查任务
        _running (bool): 管理器是否正在运行
        _last_voice_activity_time (dict[int, float]): 频道ID到最后语音活动时间的映射
        _voice_activity_sticky_duration (int): 语音活动粘滞时长（秒）
        _voice_sink (Optional[VoiceDataSink]): 语音数据接收器
        _stt_callback: STT 识别结果回调函数
    """

    def __init__(
        self,
        bot: discord.Client,
        config: "VoiceConfig",
        tts_provider: Optional[TTSProvider] = None,
        stt_provider: Optional[STTProvider] = None
    ):
        """初始化语音管理器

        Args:
            bot: Discord Bot 实例
            config: 语音配置
            tts_provider: TTS 提供商
            stt_provider: STT 提供商
        """
        self.bot = bot
        self.config = config
        self.tts_provider = tts_provider
        self.stt_provider = stt_provider

        self.enabled = config.enabled
        self.voice_channel_whitelist = config.voice_channel_whitelist
        self.check_interval = config.check_interval

        self.voice_client: Optional[VoiceClient] = None
        self._check_task: Optional[asyncio.Task] = None
        self._running = False

        # 麦克风活动追踪
        self._last_voice_activity_time: dict[int, float] = {}  # channel_id -> timestamp
        self._voice_activity_sticky_duration = 300  # 5分钟粘滞时间

        # 录音管理 (使用新的 VoiceDataSink)
        self._voice_sink: Optional[VoiceDataSink] = None
        self._stt_callback = None  # 识别结果回调函数



        logger.info(
            f"语音管理器已初始化 [启用: {self.enabled}, "
            f"白名单频道: {len(self.voice_channel_whitelist)}, "
            f"检查间隔: {self.check_interval}秒]"
        )

    async def start(self):
        """启动语音管理器"""
        if not self.enabled:
            logger.debug("语音功能未启用")
            return

        if not self.voice_channel_whitelist:
            logger.warning("语音频道白名单为空")
            return

        self._running = True

        # 单频道模式：直接连接并固定
        if len(self.voice_channel_whitelist) == 1:
            channel_id = self.voice_channel_whitelist[0]
            await self.connect(channel_id)
            logger.info(f"单频道模式：固定在频道 {channel_id}")
        else:
            # 多频道模式：启动轮询任务
            self._check_task = asyncio.create_task(self._check_loop())
            logger.info("多频道模式：启动频道切换检查")

        logger.info("语音管理器已启动")

    async def stop(self):
        """停止语音管理器

        停止后台检查任务并断开语音连接。
        """
        self._running = False
        if self._check_task:
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass
        await self.disconnect()
        logger.info("语音管理器已停止")

    async def _check_loop(self):
        """后台检查循环

        定期检查语音频道状态并更新连接（多频道模式）。

        Raises:
            asyncio.CancelledError: 当任务被取消时
        """
        try:
            while self._running:
                await self._check_and_update()
                await asyncio.sleep(self.check_interval)
        except asyncio.CancelledError:
            logger.debug("频道检查循环已取消")
        except (discord.DiscordException, RuntimeError, OSError) as e:
            logger.error(f"频道检查循环错误: {e}")

    async def _check_and_update(self):
        """检查并更新语音连接（多频道模式）

        根据频道活跃度和用户数量决定是否切换频道。
        优先保持在最近有麦克风活动的频道。
        """
        try:
            current_channel_id = self.voice_client.channel.id if self.is_connected() else None
            current_time = asyncio.get_event_loop().time()

            # 如果当前频道最近有麦克风活动，粘滞5分钟不切换
            if current_channel_id and current_channel_id in self._last_voice_activity_time:
                last_activity = self._last_voice_activity_time[current_channel_id]
                if current_time - last_activity < self._voice_activity_sticky_duration:
                    logger.debug(f"频道 {current_channel_id} 最近有语音活动，保持连接")
                    return

            # 检查当前频道人数
            if current_channel_id:
                current_channel = self.bot.get_channel(current_channel_id)
                if current_channel and isinstance(current_channel, discord.VoiceChannel):
                    human_count = sum(1 for m in current_channel.members if not m.bot)
                    if human_count > 0:
                        logger.debug(f"频道 {current_channel.name} 有 {human_count} 人，保持连接")
                        return

            # 寻找有人的频道（优先最近有语音活动的）
            target_channel = await self._find_active_channel()
            if (target_channel and
            (not current_channel_id or current_channel_id != target_channel.id)
            ):
                logger.info(f"切换到活跃频道: {target_channel.name}")
                await self.connect(target_channel.id)

        except (discord.DiscordException, RuntimeError, OSError, AttributeError) as e:
            logger.error(f"检查频道状态错误: {e}")

    async def _find_active_channel(self) -> Optional[discord.VoiceChannel]:
        """在白名单中查找最合适的频道

        优先级：
        1. 最近有麦克风活动的频道
        2. 有人的频道

        Returns:
            Optional[discord.VoiceChannel]: 最合适的语音频道,若无合适频道则返回 None
        """
        current_time = asyncio.get_event_loop().time()
        best_channel = None
        best_priority = -1

        for channel_id in self.voice_channel_whitelist:
            try:
                channel = self.bot.get_channel(channel_id)
                if not channel or not isinstance(channel, discord.VoiceChannel):
                    continue

                human_count = sum(1 for m in channel.members if not m.bot)

                # 计算优先级
                priority = 0
                if human_count > 0:
                    priority += 10

                # 最近有语音活动加高优先级
                if channel_id in self._last_voice_activity_time:
                    last_activity = self._last_voice_activity_time[channel_id]
                    if current_time - last_activity < self._voice_activity_sticky_duration:
                        priority += 100

                if priority > best_priority:
                    best_priority = priority
                    best_channel = channel

            except (discord.DiscordException, RuntimeError, AttributeError) as e:
                logger.error(f"检查频道 {channel_id} 错误: {e}")

        return best_channel

    async def on_voice_state_update(self, member, before, after):
        """处理语音状态更新事件

        检测麦克风开关状态并触发录音/识别流程。
        使用 discord-ext-voice-recv 自动接收音频数据。

        Args:
            member: 状态变更的成员
            before: 变更前的语音状态
            after: 变更后的语音状态
        """
        if not self.enabled or member.bot:
            return

        # 检测麦克风开启 (之前静音 -> 现在非静音)
        was_muted = before.self_mute or before.mute
        is_muted = after.self_mute or after.mute

        if was_muted and not is_muted and after.channel:
            # 麦克风打开
            logger.info(f"检测到用户 {member.display_name} 打开麦克风")
            logger.debug(f"开始接收 {member.display_name} 的语音数据")

        elif not was_muted and is_muted and before.channel:
            # 麦克风关闭 - 处理录音数据
            logger.info(f"检测到用户 {member.display_name} 关闭麦克风")
            if self._voice_sink:
                logger.debug("开始处理录音数据...")
                await self._process_user_audio(member)
            else:
                logger.warning("VoiceDataSink 未初始化,无法处理录音")

    async def _process_user_audio(self, member):
        """处理用户的录音数据并进行 STT 识别

        Args:
            member: 用户对象
        """
        logger.info(f"开始处理 {member.display_name} 的录音")

        if not self._voice_sink:
            logger.error("VoiceDataSink 未初始化")
            return
        if not self.stt_provider:
            logger.error("STT 提供商未初始化")
            return

        try:
            # 获取用户的音频数据
            logger.debug("从缓冲区获取音频数据...")
            audio_data = await self._voice_sink.get_audio_data(member.id)

            if not audio_data:
                logger.warning(f"用户 {member.display_name} 没有音频数据")
                return

            logger.debug(f"获取到 {len(audio_data)} bytes 音频数据")

            # 转换音频格式 (Discord PCM 48kHz -> STT需要的 16kHz)
            logger.debug("转换音频格式")
            pcm_data = convert_audio_to_pcm(audio_data)

            # 进行STT识别
            logger.info("调用 STT API 识别语音...")
            logger.debug(f"STT 提供商: {self.stt_provider.__class__.__name__}")

            text = await self.stt_provider.recognize(pcm_data)

            if text:
                logger.info(f"识别结果 [{member.display_name}]: {text}")

                # 调用回调函数
                if self._stt_callback:
                    logger.info("发送识别结果到 MaiCore")
                    await self._stt_callback(member, text)
                    logger.debug("已调用 STT 回调函数")
                else:
                    logger.warning("STT 回调函数未设置,无法发送到 MaiCore")
            else:
                logger.debug(f"用户 {member.display_name} 未识别到文本")

        except Exception as e:  # pylint: disable=broad-except
            logger.error(f"处理音频数据失败: {e}")

            logger.error(f"错误堆栈:\n{traceback.format_exc()}")

    async def connect(self, channel_id: int) -> bool:
        """连接到语音频道

        如果安装了 discord-ext-voice-recv,将使用支持录音的 VoiceClient。

        Args:
            channel_id: 目标语音频道ID

        Returns:
            bool: 连接成功返回 True,失败返回 False
        """
        if not self.enabled:
            return False

        try:
            channel = self.bot.get_channel(channel_id)
            if not channel or not isinstance(channel, discord.VoiceChannel):
                logger.error(f"频道 {channel_id} 不存在或不是语音频道")
                return False

            if self.voice_client and self.voice_client.is_connected():
                await self.voice_client.disconnect()

            # 使用支持录音的 VoiceRecvClient
            if self.stt_provider:
                self.voice_client = await channel.connect(cls=voice_recv.VoiceRecvClient)
                logger.info(f"已连接到语音频道: {channel.name} ({channel_id})")
                logger.debug("VoiceClient 类型: VoiceRecvClient (支持 STT)")

                # 启动语音接收
                await self._start_voice_receiving()
            else:
                self.voice_client = await channel.connect()
                logger.info(f"已连接到语音频道: {channel.name} ({channel_id})")
                logger.debug("VoiceClient 类型: 标准 VoiceClient (支持 TTS)")

            return True

        except (discord.DiscordException, RuntimeError, OSError) as e:
            logger.error(f"连接语音频道失败: {e}")
            return False

    async def _start_voice_receiving(self):
        """启动语音接收
        
        使用 discord-ext-voice-recv 监听语音频道中的音频数据。
        """
        logger.debug("开始启动语音接收...")

        if not self.voice_client:
            logger.error("VoiceClient 未初始化")
            return

        if not isinstance(self.voice_client, voice_recv.VoiceRecvClient):
            logger.warning("VoiceClient 不支持录音功能")
            return

        try:
            # 创建语音数据接收器
            logger.debug("创建 VoiceDataSink")
            self._voice_sink = VoiceDataSink(self)

            # 开始监听
            logger.debug("调用 listen() 开始接收音频...")
            self.voice_client.listen(self._voice_sink)
            logger.info("语音接收已启动,等待用户说话...")

        except Exception as e:  # pylint: disable=broad-except
            logger.error(f"启动语音接收失败: {e}")

            logger.error(f"错误堆栈:\n{traceback.format_exc()}")

    async def _stop_voice_receiving(self):
        """停止语音接收"""
        if not self.voice_client:
            return

        if not isinstance(self.voice_client, voice_recv.VoiceRecvClient):
            return

        try:
            logger.debug("停止语音接收...")
            self.voice_client.stop_listening()

            if self._voice_sink:
                self._voice_sink.cleanup()
                self._voice_sink = None

            logger.info("已停止语音接收")

        except Exception as e:  # pylint: disable=broad-except
            logger.error(f"停止语音接收失败: {e}")

            logger.debug(f"错误堆栈:\n{traceback.format_exc()}")

    async def disconnect(self):
        """断开语音连接

        安全地断开当前的语音频道连接并清理资源。
        """
        logger.debug("开始断开语音连接...")

        # 先停止正在播放的音频
        if self.voice_client and self.voice_client.is_playing():
            try:
                logger.debug("停止正在播放的音频...")
                self.voice_client.stop_playing()
                await asyncio.sleep(0.1)  # 给 FFmpeg 时间清理
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(f"停止播放时出现异常: {e}")

        # 停止语音接收
        await self._stop_voice_receiving()

        if self.voice_client and self.voice_client.is_connected():
            try:
                await self.voice_client.disconnect()
                logger.info("已断开语音连接")
            except (discord.DiscordException, RuntimeError, OSError) as e:
                logger.error(f"断开语音连接失败: {e}")
            except Exception as e:  # pylint: disable=broad-except
                logger.error(f"断开连接时发生未预期错误: {e}")
            finally:
                self.voice_client = None

    async def speak(self, text: str, channel_id: Optional[int] = None) -> bool:
        """播放 TTS 语音

        合成文本并在语音频道中播放。

        Args:
            text: 要播放的文本内容
            channel_id: 目标频道 ID (可选,用于切换频道)

        Returns:
            bool: 播放成功返回 True,失败返回 False
        """
        if not self.enabled or not self.tts_provider:
            return False

        try:
            # 如果指定了频道且当前未连接或连接到其他频道,先连接
            if channel_id:
                current_channel_id = self.voice_client.channel.id if self.is_connected() else None
                if current_channel_id != channel_id:
                    success = await self.connect(channel_id)
                    if not success:
                        return False

            # 检查连接状态
            if not self.voice_client or not self.voice_client.is_connected():
                logger.warning("未连接到语音频道")
                return False

            # TTS 合成
            audio_stream = await self.tts_provider.synthesize(text)
            if not audio_stream:
                logger.error("TTS 合成失败")
                return False

            # 播放音频
            audio_stream.seek(0)
            audio_source = discord.FFmpegPCMAudio(
                audio_stream,
                pipe=True,
                before_options="-f s16le -ar 48000 -ac 1",
                options="-ac 2"
            )


            if self.voice_client.is_playing():
                logger.debug("停止当前播放")
                self.voice_client.stop_playing()

            logger.info("开始播放 TTS 音频")
            self.voice_client.play(audio_source)
            logger.info(f"开始播放: {text[:50]}{'...' if len(text) > 50 else ''}")
            logger.debug(f"完整文本: {text}")
            return True

        except (discord.DiscordException, RuntimeError, OSError) as e:
            logger.error(f"播放 TTS 失败: {e}")
            return False
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f"TTS 播放时发生未预期的错误: {e}")

            logger.debug(f"错误堆栈:\n{traceback.format_exc()}")
            return False

    def is_connected(self) -> bool:
        """检查是否已连接到语音频道

        Returns:
            bool: 已连接返回 True,否则返回 False
        """
        return self.voice_client is not None and self.voice_client.is_connected()

    def get_connected_channel_id(self) -> Optional[int]:
        """获取当前连接的频道 ID

        Returns:
            Optional[int]: 当前连接的频道ID,未连接则返回 None
        """
        if self.is_connected():
            return self.voice_client.channel.id
        return None

    async def start_listening(self, callback=None):  # pylint: disable=unused-argument
        """开始监听语音频道（STT）

        Args:
            callback: 识别结果回调函数 async def callback(user_id: int, channel_id: int, text: str)

        Returns:
            bool: 总是返回 True (实际功能已被麦克风检测机制取代)

        Note:
            此方法已被 on_voice_state_update 的麦克风检测机制取代。
            现在通过检测用户麦克风开关来自动触发 STT 录音。
        """
        if not self.stt_provider:
            logger.warning("STT 提供商未初始化，无法开始监听")
            return False

        if not self.voice_client or not self.voice_client.is_connected():
            logger.warning("未连接到语音频道，无法开始监听")
            return False

        logger.info("语音监听功能通过麦克风检测自动触发，无需手动调用此方法")
        return True

    async def stop_listening(self):
        """停止监听语音频道

        停止语音接收功能（如果正在监听）。
        """
        if self.voice_client and hasattr(self.voice_client, 'stop_listening'):
            try:
                self.voice_client.stop_listening()
                logger.info("已停止语音监听")
            except AttributeError:
                logger.warning("当前 discord.py 版本可能不支持 stop_listening 方法")
        else:
            logger.debug("语音客户端未连接或不支持监听功能")

    async def recognize_audio(self, audio_data: bytes) -> Optional[str]:
        """识别音频数据（独立方法，可用于测试）

        Args:
            audio_data: PCM 音频数据

        Returns:
            Optional[str]: 识别出的文本,失败则返回 None
        """
        if not self.stt_provider:
            logger.warning("STT 提供商未初始化")
            return None

        try:
            text = await self.stt_provider.recognize(audio_data)
            return text
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f"音频识别失败: {e}")
            return None

    def set_stt_callback(self, callback):
        """设置STT识别结果回调函数

        Args:
            callback: async function(member, text) - 接收用户对象和识别文本
        """
        self._stt_callback = callback
        logger.info("已设置 STT 回调函数")

    async def close(self):
        """关闭语音管理器

        停止所有语音功能并释放资源。
        """
        await self.stop()

        if self.tts_provider:
            await self.tts_provider.close()

        if self.stt_provider:
            await self.stt_provider.close()

        logger.info("语音管理器已关闭")
