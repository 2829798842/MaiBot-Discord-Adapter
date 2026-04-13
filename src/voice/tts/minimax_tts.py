"""MiniMax TTS 提供商实现。"""

import asyncio
import wave
from io import BytesIO
from typing import Any, Optional

import aiohttp

from ..base import TTSProvider


class MiniMaxTTSProvider(TTSProvider):
    """MiniMax T2A v2 TTS 提供商。

    通过 POST /v1/t2a_v2 接口合成语音，使用最新的 voice_setting/audio_setting 结构。
    """

    def __init__(self, config: Any, logger: Any) -> None:
        """从配置读取 MiniMax API、T2A 模型、音色与音频参数并记录初始化日志。

        Args:
            config: 含 ``api_key``、``api_base``、``model``、``voice_id``、``audio_sample_rate``、``output_format`` 等属性的配置对象。
            logger: 日志器。

        Returns:
            None
        """
        self._logger = logger
        self._api_key: str = getattr(config, "api_key", "")
        self._api_base: str = getattr(config, "api_base", "https://api.minimax.io").rstrip("/")
        self._model: str = getattr(config, "model", "speech-2.8-hd")
        self._voice_id: str = getattr(config, "voice_id", "male-qn-qingse")
        self._speed: float = getattr(config, "speed", 1.0)
        self._vol: float = getattr(config, "vol", 1.0)
        self._pitch: float = getattr(config, "pitch", 0.0)
        self._sample_rate: int = getattr(config, "audio_sample_rate", 48000)
        self._audio_format: str = str(getattr(config, "output_format", "mp3") or "mp3").strip().lower()
        self._normalize_audio_options()
        self._logger.info(
            "MiniMax TTS 初始化完成 "
            f"[模型: {self._model}, 音色: {self._voice_id}, 格式: {self._audio_format}, 采样率: {self._sample_rate}]"
        )

    def _normalize_audio_options(self) -> None:
        """按官方支持范围修正采样率和音频格式。"""
        valid_formats = {"pcm", "mp3", "flac", "wav"}
        if self._audio_format not in valid_formats:
            self._logger.warning(
                f"MiniMax TTS 音频格式不受支持: {self._audio_format!r}，改用 mp3"
            )
            self._audio_format = "mp3"

        valid_sample_rates = {8000, 16000, 22050, 24000, 32000, 44100}
        if self._sample_rate not in valid_sample_rates:
            self._logger.warning(
                f"MiniMax TTS 采样率不受支持: {self._sample_rate}，改用 32000"
            )
            self._sample_rate = 32000

    def _wrap_pcm_as_wav(self, data: bytes) -> bytes:
        """将裸 PCM 字节包装为 WAV，避免播放阶段按错采样率。"""
        wav_buffer = BytesIO()
        with wave.open(wav_buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self._sample_rate)
            wav_file.writeframes(data)
        return wav_buffer.getvalue()

    async def synthesize(self, text: str) -> Optional[BytesIO]:
        """调用 MiniMax ``POST /v1/t2a_v2``，将返回的 hex 编码音频解码为字节并封装为 ``BytesIO``。

        Args:
            text: 待合成文本。

        Returns:
            成功时返回音频字节的 ``BytesIO``；缺密钥、空文本、响应缺字段或请求失败时返回 None。
        """
        if not text:
            return None
        if not self._api_key:
            self._logger.error("MiniMax API 密钥未配置")
            return None

        url = f"{self._api_base}/v1/t2a_v2"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "text": text,
            "stream": False,
            "output_format": "hex",
            "voice_setting": {
                "voice_id": self._voice_id,
                "speed": self._speed,
                "vol": self._vol,
                "pitch": self._pitch,
            },
            "audio_setting": {
                "sample_rate": self._sample_rate,
                "bitrate": 128000,
                "format": self._audio_format,
                "channel": 1,
            },
        }

        try:
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        base_resp = result.get("base_resp", {})
                        status_code = base_resp.get("status_code", 0)
                        if status_code:
                            status_msg = base_resp.get("status_msg", "unknown error")
                            self._logger.error(
                                f"MiniMax TTS 返回业务错误: status_code={status_code}, status_msg={status_msg}"
                            )
                            return None
                        audio_hex = result.get("data", {}).get("audio", "")
                        if not audio_hex:
                            self._logger.error("MiniMax TTS 响应中缺少音频数据")
                            return None
                        audio_bytes = bytes.fromhex(audio_hex)
                        if self._audio_format == "pcm":
                            audio_bytes = self._wrap_pcm_as_wav(audio_bytes)
                        self._logger.debug(
                            f"MiniMax TTS 合成成功: {text[:50]}... ({len(audio_bytes)} bytes)"
                        )
                        return BytesIO(audio_bytes)
                    text_err = await resp.text()
                    self._logger.error(f"MiniMax TTS 请求失败: {resp.status} {text_err}")
                    return None
        except asyncio.TimeoutError:
            self._logger.error("MiniMax TTS 请求超时")
            return None
        except aiohttp.ClientError as exc:
            self._logger.error(f"MiniMax TTS 网络错误: {exc}")
            return None
        except Exception as exc:
            self._logger.error(f"MiniMax TTS 异常: {exc}")
            return None

    async def close(self) -> None:
        """关闭提供商（当前实现仅打日志，无持久连接需释放）。

        Returns:
            None
        """
        self._logger.info("MiniMax TTS 提供商已关闭")
