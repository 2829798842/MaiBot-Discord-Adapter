"""第三方 AcgNAI TTS 提供商实现"""

from typing import Optional
from io import BytesIO
import asyncio
import aiohttp
from src.voice.base import TTSProvider
from src.logger import logger as base_logger

logger = base_logger.bind(name="AcgNAITTS")


class AcgNAITTSProvider(TTSProvider):
    """AcgNAI TTS 提供商（https://tts.acgnai.top）"""

    def __init__(self, config):
        self.config = config
        self.api_base = config.api_base.rstrip('/') if getattr(config, 'api_base', None) else 'https://tts.acgnai.top'
        self.api_key = getattr(config, 'api_key', None)

        logger.info(f"AcgNAI TTS 初始化完成 [api_base={self.api_base}]")

    async def synthesize(self, text: str) -> Optional[BytesIO]:
        """合成语音"""
        if not text:
            return None

        try:
            url = f"{self.api_base}/api/tts"
            headers = {}
            if self.api_key:
                headers['Authorization'] = f"Bearer {self.api_key}"

            payload = {"text": text}
            timeout = aiohttp.ClientTimeout(total=30)

            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        logger.debug(f"TTS 合成成功: {text[:50]}...")
                        return BytesIO(data)
                    else:
                        text_err = await resp.text()
                        logger.error(f"AcgNAI TTS 请求失败: {resp.status} {text_err}")
                        return None

        except asyncio.TimeoutError:
            logger.error("AcgNAI TTS 请求超时")
            return None
        except aiohttp.ClientError as e:
            logger.error(f"AcgNAI TTS 网络错误: {e}")
            return None
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f"AcgNAI TTS 异常: {e}")
            return None

    async def close(self):
        """关闭资源"""
        logger.info("AcgNAI TTS 提供商已关闭")
