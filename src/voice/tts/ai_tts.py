"""AI Hobbyist TTS 提供商实现 (GPT-SoVITS v4)"""

import random
from typing import Optional
from io import BytesIO
import asyncio
import traceback
import aiohttp
from src.voice.base import TTSProvider
from src.logger import logger as base_logger

logger = base_logger.bind(name="AI_TTS")


class AITTSProvider(TTSProvider):
    """AI Hobbyist TTS 提供商 (https://gsv.acgnai.top)
    
    使用 AI Hobbyist 的 GPT-SoVITS v4 API
    支持《崩坏3》《原神》《星穹铁道》《鸣潮》等角色语音
    """

    def __init__(self, config):
        self.config = config
        # API v4 基础 URL
        self.api_base = getattr(config, 'api_base', 'https://gsv2p.acgnai.top').rstrip('/')
        # API Token (从网站右上角用户名处获取)
        self.api_token = getattr(config, 'api_token', None)
        # 默认模型配置
        self.model_name = getattr(config, 'model_name', '崩环三-中文-爱莉希雅')
        self.language = getattr(config, 'language', '中文')
        self.emotion = getattr(config, 'emotion', '默认')

        # 请求头
        self.headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }
        if self.api_token:
            self.headers['Authorization'] = f"Bearer {self.api_token}"

        # 可用模型缓存
        self._models_cache = None

        logger.info("AI Hobbyist TTS 初始化完成")
        logger.debug(f"  └─ API Base: {self.api_base}")
        logger.debug(f"  └─ Token: {'已配置' if self.api_token else '未配置'}")
        logger.debug(f"  └─ 默认模型: {self.model_name}")
        logger.debug(f"  └─ 默认语言: {self.language}")
        logger.debug(f"  └─ 默认语气: {self.emotion}")

    async def get_models(self) -> dict:
        """获取所有可用的语音模型
        
        Returns:
            dict: {模型名: {语言: [语气列表]}}
        """
        if self._models_cache:
            return self._models_cache

        try:
            url = f"{self.api_base}/models/v4"
            timeout = aiohttp.ClientTimeout(total=10)

            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, headers=self.headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        models = data.get('models', {})
                        self._models_cache = models
                        logger.debug(f"获取到 {len(models)} 个可用模型")
                        return models
                    logger.error(f"获取模型列表失败: HTTP {resp.status}")
                    return {}

        except Exception as e:  # pylint: disable=broad-except
            logger.error(f"获取模型列表异常: {e}")
            return {}

    async def synthesize(self, text: str) -> Optional[BytesIO]:
        """合成语音
        
        Args:
            text: 要合成的文本
            
        Returns:
            BytesIO: 音频数据 (WAV 格式)
        """
        if not text:
            return None

        if not self.api_token:
            logger.error("AI Hobbyist TTS Token 未配置，无法使用")
            return None

        try:
            # 构建请求 payload
            payload = {
                'version': 'v4',
                'model_name': self.model_name,
                'prompt_text_lang': self.language,
                'emotion': self.emotion,
                'text': text,
                'text_lang': self.language,
                'top_k': 10,
                'top_p': 1,
                'temperature': 1,
                'text_split_method': '按标点符号切',
                'batch_size': 1,
                'batch_threshold': 0.75,
                'split_bucket': True,
                'speed_facter': 1,
                'fragment_interval': 0.3,
                'media_type': 'wav',
                'parallel_infer': True,
                'repetition_penalty': 1.35,
                'seed': random.randint(0, 999999999),
                'sample_steps': 16,
                'if_sr': False,
            }

            url = f"{self.api_base}/infer_single"
            timeout = aiohttp.ClientTimeout(total=60)

            logger.debug(f"请求 TTS: 模型={self.model_name}, 语言={self.language}, 语气={self.emotion}")
            logger.debug(f"文本: {text[:100]}{'...' if len(text) > 100 else ''}")

            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers=self.headers) as resp:
                    if resp.status != 200:
                        text_err = await resp.text()
                        logger.error(f"TTS 请求失败: HTTP {resp.status} - {text_err}")
                        return None

                    data = await resp.json()

                    # 检查响应
                    if data.get('msg') == '参数错误':
                        logger.error(f"TTS 参数错误: 模型={self.model_name}, "
                                     f"语言={self.language}, 语气={self.emotion}"
                                     )
                        return None

                    if data.get('msg') == '合成成功':
                        audio_url = data.get('audio_url')
                        if not audio_url:
                            logger.error("TTS 响应中缺少 audio_url")
                            return None

                        logger.debug(f"TTS 合成成功，下载音频: {audio_url}")

                        # 下载音频文件
                        async with session.get(audio_url) as audio_resp:
                            if audio_resp.status == 200:
                                audio_data = await audio_resp.read()
                                logger.info(f"✓ TTS 合成成功: {len(audio_data)} bytes")
                                return BytesIO(audio_data)
                            logger.error(f"下载音频失败: HTTP {audio_resp.status}")
                            return None

                    logger.error(f"TTS 未知错误: {data}")
                    return None

        except asyncio.TimeoutError:
            logger.error("TTS 请求超时")
            return None
        except aiohttp.ClientError as e:
            logger.error(f"TTS 网络错误: {e}")
            return None
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f"TTS 异常: {e}")


            logger.debug(f"错误堆栈:\n{traceback.format_exc()}")
            return None

    async def close(self):
        """关闭资源"""
        logger.info("AI Hobbyist TTS 提供商已关闭")
