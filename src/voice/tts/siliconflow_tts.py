"""SiliconFlow TTS 提供商实现"""

from typing import Optional
from io import BytesIO
import asyncio
import aiohttp
from src.voice.base import TTSProvider
from src.logger import logger as base_logger

logger = base_logger.bind(name="SiliconFlowTTS")


class SiliconFlowTTSProvider(TTSProvider):
    """SiliconFlow TTS 提供商
    
    使用 SiliconFlow MOSS-TTSD 模型进行语音合成
    支持中英文对话合成
    """

    def __init__(self, config):
        self.config = config
        self.api_key = config.api_key
        self.api_base = config.api_base.rstrip('/')
        self.model = getattr(config, 'tts_model', 'fnlp/MOSS-TTSD-v0.5')
        self.voice = getattr(config, 'tts_voice', 'fnlp/MOSS-TTSD-v0.5:alex')
        self.response_format = getattr(config, 'response_format', 'pcm')
        self.sample_rate = getattr(config, 'sample_rate', 48000)
        self.speed = getattr(config, 'speed', 1.0)

        logger.info(
            f"SiliconFlow TTS 初始化完成 [模型: {self.model}, "
            f"音色: {self.voice}, 格式: {self.response_format}]"
        )

    async def synthesize(self, text: str) -> Optional[BytesIO]:
        """合成语音"""
        if not text:
            return None

        if not self.api_key:
            logger.error("SiliconFlow API 密钥未配置")
            return None

        try:
            url = f"{self.api_base}/audio/speech"
            headers = {
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json'
            }

            if '[S1]' not in text and '[S2]' not in text:
                text = f'[S1]{text}'

            payload = {
                'model': self.model,
                'input': text,
                'voice': self.voice,
                'response_format': self.response_format,
                'sample_rate': self.sample_rate,
                'speed': self.speed,
                'stream': False
            }

            timeout = aiohttp.ClientTimeout(total=60)

            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        logger.debug(f"TTS 合成成功: {text[:50]}... (音频大小: {len(data)} bytes)")
                        return BytesIO(data)
                    text_err = await resp.text()
                    logger.error(f"SiliconFlow TTS 请求失败: {resp.status} {text_err}")
                    return None

        except asyncio.TimeoutError:
            logger.error("SiliconFlow TTS 请求超时")
            return None
        except aiohttp.ClientError as e:
            logger.error(f"SiliconFlow TTS 网络错误: {e}")
            return None
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f"SiliconFlow TTS 异常: {e}")
            return None

    async def close(self):
        """关闭资源"""
        logger.info("SiliconFlow TTS 提供商已关闭")
