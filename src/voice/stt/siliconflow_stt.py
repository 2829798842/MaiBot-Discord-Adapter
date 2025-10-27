"""SiliconFlow STT 提供商实现"""

from typing import Optional
import asyncio
import aiohttp
from src.voice.base import STTProvider
from src.logger import logger as base_logger

logger = base_logger.bind(name="SiliconFlowSTT")


class SiliconFlowSTTProvider(STTProvider):
    """SiliconFlow STT 提供商"""

    def __init__(self, config):
        self.config = config
        self.api_key = config.api_key
        self.api_base = config.api_base.rstrip('/')
        self.model = getattr(config, 'stt_model', 'FunAudioLLM/SenseVoiceSmall')

        logger.info(f"SiliconFlow STT 初始化完成 [模型: {self.model}]")

    async def recognize(self, audio_data: bytes) -> Optional[str]:
        """识别语音"""
        if not audio_data:
            return None

        if not self.api_key:
            logger.error("SiliconFlow API 密钥未配置")
            return None

        try:
            url = f"{self.api_base}/audio/transcriptions"
            headers = {
                'Authorization': f'Bearer {self.api_key}'
            }

            form = aiohttp.FormData()
            form.add_field('model', self.model)
            form.add_field(
                'file',
                audio_data,
                filename='audio.wav',
                content_type='audio/wav'
            )

            timeout = aiohttp.ClientTimeout(total=60)

            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, data=form, headers=headers) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        text = result.get('text', '')

                        if text:
                            logger.debug(f"识别结果: {text}")
                            return text
                        logger.debug("未识别到语音")
                        return None
                    text_err = await resp.text()
                    logger.error(f"SiliconFlow STT 请求失败: {resp.status} {text_err}")
                    return None

        except asyncio.TimeoutError:
            logger.error("SiliconFlow STT 请求超时")
            return None
        except aiohttp.ClientError as e:
            logger.error(f"SiliconFlow STT 网络错误: {e}")
            return None
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f"SiliconFlow STT 异常: {e}")
            return None

    async def close(self):
        """关闭资源"""
        logger.info("SiliconFlow STT 提供商已关闭")
