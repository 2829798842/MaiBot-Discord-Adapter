"""AI Hobbyist TTS 实现.
"""
from __future__ import annotations

import asyncio
import copy
import random
import traceback
from io import BytesIO
from typing import Any, Dict, Optional

import aiohttp

from src.logger import logger as base_logger
from src.voice.base import TTSProvider
from src.config.voice_config import AIHobbyistVoiceConfig

logger = base_logger.bind(name="AI_Hobbyist_TTS")


class AITTSProvider(TTSProvider):
    """AI Hobbyist TTS 实现。

    该实现与 https://tts.acgnai.top/ 的 `infer_single` 接口对接，
    

    相关作者:
        GPT-SoVITS开发者：@花儿不哭
        模型训练者：@红血球AE3803 @白菜工厂1145号员工
        推理特化包适配 & 在线推理：@AI-Hobbyist
    """

    VERSION: str = "v4"
    MODELS_ENDPOINT: str = "/models/{version}"
    INFER_ENDPOINT: str = "/infer_single"
    MODELS_TIMEOUT_SECONDS: int = 10
    INFER_TIMEOUT_SECONDS: int = 60
    BASE_HEADERS: Dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    BASE_PAYLOAD_TEMPLATE: Dict[str, Any] = {
        "version": VERSION,
        "model_name": "",
        "prompt_text_lang": "",
        "emotion": "",
        "text": "",
        "text_lang": "",
        "top_k": 10,
        "top_p": 1,
        "temperature": 1,
        "text_split_method": "按标点符号切",
        "batch_size": 1,
        "batch_threshold": 0.75,
        "split_bucket": True,
        "speed_facter": 1,
        "fragment_interval": 0.3,
        "media_type": "wav",
        "parallel_infer": True,
        "repetition_penalty": 1.35,
        "seed": -1,
        "sample_steps": 16,
        "if_sr": False,
    }

    def __init__(self, config: AIHobbyistVoiceConfig) -> None:
        self.config: AIHobbyistVoiceConfig = config
        self.api_base: str = getattr(config, "api_base", "https://gsv2p.acgnai.top").rstrip("/")
        self.api_token: Optional[str] = getattr(config, "api_token", None)
        self.model_name: str = getattr(config, "model_name", "崩坏三-中文-爱莉希雅_ZH")
        self.language: str = getattr(config, "language", "中文")
        self.emotion: str = getattr(config, "emotion", "默认")

        self.headers: Dict[str, str] = copy.deepcopy(self.BASE_HEADERS)
        if self.api_token:
            self.headers["Authorization"] = f"Bearer {self.api_token}"

        self._models_cache: Optional[dict] = None

        logger.info("AI Hobbyist TTS 初始化完成")
        logger.debug(f"  └─ API Base: {self.api_base}")
        logger.debug(f"  └─ Token: {'已配置' if self.api_token else '未配置'}")
        logger.debug(f"  └─ 默认模型: {self.model_name}")
        logger.debug(f"  └─ 默认语言: {self.language}")
        logger.debug(f"  └─ 默认语气: {self.emotion}")

    async def get_models(self) -> dict:
        """获取所有可用的语音模型。

        Returns:
            dict: {模型名: {语言: [语气列表]}}
        """
        if self._models_cache is not None:
            return self._models_cache

        try:
            url: str = f"{self.api_base}{self.MODELS_ENDPOINT.format(version=self.VERSION)}"
            timeout = aiohttp.ClientTimeout(total=self.MODELS_TIMEOUT_SECONDS)

            # 使用 GET 请求获取模型列表 (根据API文档)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=self.headers) as resp:
                    if resp.status == 200:
                        data: Dict[str, Any] = await resp.json()
                        models: Dict[str, Any] = data.get("models", {})
                        self._models_cache = models
                        logger.info(f"成功获取 {len(models)} 个可用模型")
                        return models

                    error_text = await resp.text()
                    logger.error(f"获取模型列表失败: HTTP {resp.status} - {error_text}")
                    return {}

        except Exception as exc:  # pylint: disable=broad-except
            logger.error(f"获取模型列表异常: {exc}")
            logger.debug(f"错误堆栈:\n{traceback.format_exc()}")
            return {}

    async def _ensure_valid_params(self) -> bool:
        """确保模型参数有效,如果无法获取模型列表则使用配置的参数。

        Returns:
            bool: 参数有效返回 True
        """
        try:
            models = await self.get_models()

            if not models:
                logger.warning("无法获取 AI Hobbyist 模型列表, 将直接使用配置的参数, 可能导致合成失败")
                return True  # 无法验证,但继续使用配置的参数

            # 检查模型是否存在
            if self.model_name not in models:
                available_models = list(models.keys())[:10]  # 显示前10个可用模型
                logger.warning(
                    f"配置的模型 '{self.model_name}' 不在可用列表中, 将直接使用, 可能导致合成失败. "
                    f"可用模型示例: {available_models}"
                )
                return True

            model_langs = models[self.model_name]

            # 检查语言是否存在
            if self.language not in model_langs:
                available_langs = list(model_langs.keys())
                logger.warning(
                    f"模型 '{self.model_name}' 不支持语言 '{self.language}', 将直接使用, 可能导致合成失败. "
                    f"可用语言: {available_langs}"
                )
                return True

            # 检查语气是否存在
            emotions = model_langs[self.language]
            if self.emotion not in emotions:
                logger.warning(
                    f"模型 '{self.model_name}' 的语言 '{self.language}' 不支持语气 '{self.emotion}', "
                    f"将直接使用, 可能导致合成失败. 可用语气: {emotions}"
                )
                return True

            logger.debug(f"参数验证通过: 模型={self.model_name}, 语言={self.language}, 语气={self.emotion}")
            return True

        except Exception as exc:  # pylint: disable=broad-except
            logger.error(f"参数验证异常: {exc}")
            logger.debug(f"错误堆栈:\n{traceback.format_exc()}")
            return True  # 即使验证失败也继续,让API返回错误

    async def synthesize(self, text: str) -> Optional[BytesIO]:
        """合成语音。

        Args:
            text: 要合成的文本。

        Returns:
            Optional[BytesIO]: 返回 WAV 音频数据，失败时返回 None。
        """
        if not text:
            return None

        if not self.api_token:
            logger.error("AI Hobbyist TTS Token 未配置，无法使用")
            return None

        # 验证参数有效性
        await self._ensure_valid_params()

        payload: Dict[str, Any] = copy.deepcopy(self.BASE_PAYLOAD_TEMPLATE)
        payload.update(
            {
                "model_name": self.model_name,
                "prompt_text_lang": self.language,
                "emotion": self.emotion,
                "text": text,
                "text_lang": self.language,
                "seed": random.randint(0, 999_999_999),
            }
        )

        url: str = f"{self.api_base}{self.INFER_ENDPOINT}"
        timeout = aiohttp.ClientTimeout(total=self.INFER_TIMEOUT_SECONDS)

        logger.debug(f"请求 TTS: 模型={self.model_name}, 语言={self.language}, 语气={self.emotion}")
        preview_text: str = text[:100] + ("..." if len(text) > 100 else "")
        logger.debug(f"文本: {preview_text}")

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers=self.headers) as resp:
                    if resp.status != 200:
                        text_err = await resp.text()
                        logger.error("TTS 请求失败: HTTP %s - %s", resp.status, text_err)
                        return None

                    data: Dict[str, Any] = await resp.json()

                    if data.get("msg") == "参数错误":
                        logger.error(
                            f"TTS 参数错误: 模型={self.model_name}, 语言={self.language}, 语气={self.emotion}"
                        )
                        return None

                    if data.get("msg") == "合成成功":
                        audio_url: Optional[str] = data.get("audio_url")
                        if not audio_url:
                            logger.error("TTS 响应中缺少 audio_url")
                            return None

                        logger.debug(f"TTS 合成成功，下载音频: {audio_url}")

                        async with session.get(audio_url) as audio_resp:
                            if audio_resp.status == 200:
                                audio_data: bytes = await audio_resp.read()
                                logger.info(f"TTS 合成成功: {len(audio_data)} bytes")
                                return BytesIO(audio_data)

                            logger.error(f"下载音频失败: HTTP {audio_resp.status}")
                            return None

                    logger.error(f"TTS 未知错误: {data}")
                    return None

        except asyncio.TimeoutError:
            logger.error("TTS 请求超时")
            return None
        except aiohttp.ClientError as exc:
            logger.error(f"TTS 网络错误: {exc}")
            return None
        except Exception as exc:  # pylint: disable=broad-except
            logger.error(f"TTS 异常: {exc}")
            logger.debug(f"错误堆栈:\n{traceback.format_exc()}")
            return None

    async def close(self) -> None:
        """关闭资源。"""
        logger.info("AI Hobbyist TTS 提供商已关闭")
