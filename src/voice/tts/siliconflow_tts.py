"""SiliconFlow TTS 提供商实现。"""

import asyncio
import wave
from io import BytesIO
from typing import Any, Optional

import aiohttp

from ..base import TTSProvider


class SiliconFlowTTSProvider(TTSProvider):
    """SiliconFlow TTS 提供商。

    通过 /audio/speech 接口合成语音。
    """

    def __init__(self, config: Any, logger: Any) -> None:
        """从配置对象读取 API、模型与音色等参数并记录初始化日志。

        Args:
            config: 含 ``api_key``、``api_base``、``model``、``voice`` 等属性的配置对象。
            logger: 日志器。

        Returns:
            None
        """
        self._logger = logger
        self._api_key: str = getattr(config, "api_key", "")
        self._api_base: str = getattr(config, "api_base", "https://api.siliconflow.cn/v1").rstrip("/")
        self._model: str = getattr(config, "model", "fnlp/MOSS-TTSD-v0.5")
        self._voice: str = getattr(config, "voice", "fnlp/MOSS-TTSD-v0.5:alex")
        self._response_format: str = str(getattr(config, "response_format", "wav") or "wav").strip().lower()
        self._sample_rate: int = int(getattr(config, "sample_rate", 32000))
        self._speed: float = getattr(config, "speed", 1.0)
        self._normalize_audio_options()
        self._logger.info(
            "SiliconFlow TTS 初始化完成 "
            f"[模型: {self._model}, 音色: {self._voice}, 格式: {self._response_format}, 采样率: {self._sample_rate}]"
        )

    def _normalize_audio_options(self) -> None:
        """按官方约束修正输出格式与采样率组合。"""
        if self._response_format not in {"mp3", "opus", "wav", "pcm"}:
            self._logger.warning(
                f"SiliconFlow TTS response_format 不受支持: {self._response_format!r}，改用 wav"
            )
            self._response_format = "wav"

        if self._response_format == "opus":
            if self._sample_rate != 48000:
                self._logger.warning(
                    f"SiliconFlow TTS opus 仅支持 48000Hz，已从 {self._sample_rate} 调整为 48000"
                )
                self._sample_rate = 48000
            return

        valid_sample_rates = {
            "mp3": {32000, 44100},
            "wav": {8000, 16000, 24000, 32000, 44100},
            "pcm": {8000, 16000, 24000, 32000, 44100},
        }
        allowed = valid_sample_rates.get(self._response_format, {32000})
        if self._sample_rate not in allowed:
            fallback = 44100 if 44100 in allowed else min(allowed)
            self._logger.warning(
                "SiliconFlow TTS 采样率与输出格式不匹配，已自动调整: "
                f"format={self._response_format}, sample_rate={self._sample_rate} -> {fallback}"
            )
            self._sample_rate = fallback

    def _wrap_pcm_as_wav(self, data: bytes) -> bytes:
        """将裸 PCM 字节包装为 WAV，避免播放阶段丢失采样率信息。"""
        wav_buffer = BytesIO()
        with wave.open(wav_buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self._sample_rate)
            wav_file.writeframes(data)
        return wav_buffer.getvalue()

    async def synthesize(self, text: str) -> Optional[BytesIO]:
        """调用 SiliconFlow ``/audio/speech`` 接口将文本合成为音频（默认 PCM 等格式）。

        Args:
            text: 待合成文本；若无 ``[S1]``/``[S2]`` 标记会自动加上 ``[S1]`` 前缀。

        Returns:
            成功时返回含响应体的 ``BytesIO``；缺密钥、空文本、HTTP 或网络错误时返回 None。
        """
        if not text:
            return None
        if not self._api_key:
            self._logger.error("SiliconFlow API 密钥未配置")
            return None

        if "[S1]" not in text and "[S2]" not in text:
            text = f"[S1]{text}"

        url = f"{self._api_base}/audio/speech"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "input": text,
            "voice": self._voice,
            "response_format": self._response_format,
            "sample_rate": self._sample_rate,
            "speed": self._speed,
            "stream": False,
        }

        try:
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        if self._response_format == "pcm":
                            data = self._wrap_pcm_as_wav(data)
                        self._logger.debug(
                            f"SiliconFlow TTS 合成成功: {text[:50]}... ({len(data)} bytes)"
                        )
                        return BytesIO(data)
                    text_err = await resp.text()
                    self._logger.error(f"SiliconFlow TTS 请求失败: {resp.status} {text_err}")
                    return None
        except asyncio.TimeoutError:
            self._logger.error("SiliconFlow TTS 请求超时")
            return None
        except aiohttp.ClientError as exc:
            self._logger.error(f"SiliconFlow TTS 网络错误: {exc}")
            return None
        except Exception as exc:
            self._logger.error(f"SiliconFlow TTS 异常: {exc}")
            return None

    async def close(self) -> None:
        """关闭提供商（当前实现仅打日志，无持久连接需释放）。

        Returns:
            None
        """
        self._logger.info("SiliconFlow TTS 提供商已关闭")
