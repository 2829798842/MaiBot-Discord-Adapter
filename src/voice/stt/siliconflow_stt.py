"""SiliconFlow STT (SenseVoice) 提供商实现。"""

import asyncio
import struct
from typing import Any, Optional

import aiohttp

from ..base import STTProvider


class SiliconFlowSTTProvider(STTProvider):
    """SiliconFlow STT 提供商。

    通过 /audio/transcriptions 接口进行语音识别。
    输入的裸 PCM 数据会自动包装为 WAV 格式后上传。
    """

    def __init__(self, config: Any, logger: Any) -> None:
        """从配置读取 SiliconFlow API 与转写模型并记录初始化日志。

        Args:
            config: 含 ``api_key``、``api_base``、``model`` 等属性的配置对象。
            logger: 日志器。

        Returns:
            None
        """
        self._logger = logger
        self._api_key: str = getattr(config, "api_key", "")
        self._api_base: str = getattr(config, "api_base", "https://api.siliconflow.cn/v1").rstrip("/")
        self._model: str = getattr(config, "model", "FunAudioLLM/SenseVoiceSmall")
        self._logger.info(f"SiliconFlow STT 初始化完成 [模型: {self._model}]")

    async def recognize(self, audio_data: bytes) -> Optional[str]:
        """将 16kHz 单声道 PCM 包装为 WAV 后，调用 ``/audio/transcriptions`` 上传识别。

        Args:
            audio_data: 裸 PCM 字节（通常为 16kHz/mono/s16le，与 ``_wrap_pcm_as_wav`` 参数一致）。

        Returns:
            识别到的 ``text`` 字段字符串；无数据、缺密钥、无识别结果或请求失败时返回 None。
        """
        if not audio_data:
            return None
        if not self._api_key:
            self._logger.error("SiliconFlow API 密钥未配置")
            return None

        wav_data = _wrap_pcm_as_wav(audio_data, sample_rate=16000, channels=1, bits_per_sample=16)

        try:
            url = f"{self._api_base}/audio/transcriptions"
            headers = {"Authorization": f"Bearer {self._api_key}"}

            form = aiohttp.FormData()
            form.add_field("model", self._model)
            form.add_field(
                "file", wav_data, filename="audio.wav", content_type="audio/wav"
            )

            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, data=form, headers=headers) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        text = result.get("text", "")
                        if text:
                            self._logger.debug(f"识别结果: {text}")
                            return text
                        self._logger.debug("未识别到语音")
                        return None
                    text_err = await resp.text()
                    self._logger.error(f"SiliconFlow STT 请求失败: {resp.status} {text_err}")
                    return None
        except asyncio.TimeoutError:
            self._logger.error("SiliconFlow STT 请求超时")
            return None
        except aiohttp.ClientError as exc:
            self._logger.error(f"SiliconFlow STT 网络错误: {exc}")
            return None
        except Exception as exc:
            self._logger.error(f"SiliconFlow STT 异常: {exc}")
            return None

    async def close(self) -> None:
        """关闭提供商（当前实现仅打日志，无持久连接需释放）。

        Returns:
            None
        """
        self._logger.info("SiliconFlow STT 提供商已关闭")


def _wrap_pcm_as_wav(
    pcm_data: bytes,
    sample_rate: int = 16000,
    channels: int = 1,
    bits_per_sample: int = 16,
) -> bytes:
    """将裸 PCM 样本数据包装为带 RIFF/WAVE 头的完整 WAV 字节流。

    Args:
        pcm_data: 小端有符号 16 位 PCM 样本连续字节。
        sample_rate: 采样率（Hz），默认 16000。
        channels: 声道数，默认 1。
        bits_per_sample: 每采样位数，默认 16。

    Returns:
        可直接作为 ``audio/wav`` 文件上传的完整 WAV 数据。
    """
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    data_size = len(pcm_data)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        data_size,
    )
    return header + pcm_data
