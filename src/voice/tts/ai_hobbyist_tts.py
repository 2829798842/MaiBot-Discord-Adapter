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
        self.model_name: str = getattr(config, "model_name", "崩环三-中文-爱莉希雅")
        self.language: str = getattr(config, "language", "中文")
        self.emotion: str = getattr(config, "emotion", "默认")

        self.headers: Dict[str, str] = copy.deepcopy(self.BASE_HEADERS)
        if self.api_token:
            self.headers["Authorization"] = f"Bearer {self.api_token}"

        self._models_cache: Optional[dict] = None
        self._params_validated: bool = False

        logger.info("AI Hobbyist TTS 初始化完成")
        logger.debug("  └─ API Base: %s", self.api_base)
        logger.debug("  └─ Token: %s", "已配置" if self.api_token else "未配置")
        logger.debug("  └─ 默认模型: %s", self.model_name)
        logger.debug("  └─ 默认语言: %s", self.language)
        logger.debug("  └─ 默认语气: %s", self.emotion)

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

            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, headers=self.headers) as resp:
                    if resp.status == 200:
                        data: Dict[str, Any] = await resp.json()
                        models: Dict[str, Any] = data.get("models", {})
                        self._models_cache = models
                        logger.debug("获取到 %d 个可用模型", len(models))
                        return models

                    logger.error("获取模型列表失败: HTTP %s", resp.status)
                    return {}

        except Exception as exc:  # pylint: disable=broad-except
            logger.error("获取模型列表异常: %s", exc)
            return {}

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

        logger.debug("请求 TTS: 模型=%s, 语言=%s, 语气=%s", self.model_name, self.language, self.emotion)
        preview_text: str = text[:100] + ("..." if len(text) > 100 else "")
        logger.debug("文本: %s", preview_text)

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
                            "TTS 参数错误: 模型=%s, 语言=%s, 语气=%s",
                            self.model_name,
                            self.language,
                            self.emotion,
                        )
                        return None

                    if data.get("msg") == "合成成功":
                        audio_url: Optional[str] = data.get("audio_url")
                        if not audio_url:
                            logger.error("TTS 响应中缺少 audio_url")
                            return None

                        logger.debug("TTS 合成成功，下载音频: %s", audio_url)

                        async with session.get(audio_url) as audio_resp:
                            if audio_resp.status == 200:
                                audio_data: bytes = await audio_resp.read()
                                logger.info("TTS 合成成功: %d bytes", len(audio_data))
                                return BytesIO(audio_data)

                            logger.error("下载音频失败: HTTP %s", audio_resp.status)
                            return None

                    logger.error("TTS 未知错误: %s", data)
                    return None

        except asyncio.TimeoutError:
            logger.error("TTS 请求超时")
            return None
        except aiohttp.ClientError as exc:
            logger.error("TTS 网络错误: %s", exc)
            return None
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("TTS 异常: %s", exc)
            logger.debug("错误堆栈:\n%s", traceback.format_exc())
            return None

    async def close(self) -> None:
        """关闭资源。"""
        logger.info("AI Hobbyist TTS 提供商已关闭")

    async def _ensure_valid_params(self) -> None:
        """校验并在必要时纠正配置的模型/语言/语气组合。"""

        if self._params_validated:
            return

        models = await self.get_models()
        if not models:
            logger.warning("无法获取 AI Hobbyist 模型列表，将直接使用配置的参数，可能导致合成失败")
            self._params_validated = True
            return

        updated = False

        if self.model_name not in models:
            previous_model = self.model_name
            self.model_name, languages_map = next(iter(models.items()))
            self.config.model_name = self.model_name
            updated = True
            logger.warning(
                "配置的 AI Hobbyist 模型 '%s' 不存在，改用 '%s'",
                previous_model,
                self.model_name,
            )
        else:
            languages_map = models.get(self.model_name, {})

        if not languages_map:
            logger.warning("模型 '%s' 没有提供可用语言，保留当前语言 '%s' 并继续", self.model_name, self.language)
        else:
            if self.language not in languages_map:
                previous_language = self.language
                self.language = next(iter(languages_map.keys()))
                self.config.language = self.language
                updated = True
                logger.warning(
                    "模型 '%s' 不支持语言 '%s'，改用 '%s'",
                    self.model_name,
                    previous_language,
                    self.language,
                )

            emotions = languages_map.get(self.language, [])
            if not emotions:
                logger.warning(
                    "模型 '%s' 在语言 '%s' 下没有语气列表，保留当前语气 '%s'",
                    self.model_name,
                    self.language,
                    self.emotion,
                )
            elif self.emotion not in emotions:
                previous_emotion = self.emotion
                self.emotion = emotions[0]
                self.config.emotion = self.emotion
                updated = True
                logger.warning(
                    "模型 '%s' / 语言 '%s' 不支持语气 '%s'，改用 '%s'",
                    self.model_name,
                    self.language,
                    previous_emotion,
                    self.emotion,
                )

        if updated:
            logger.info(
                "AI Hobbyist TTS 参数已自动调整: 模型=%s, 语言=%s, 语气=%s",
                self.model_name,
                self.language,
                self.emotion,
            )

        self._params_validated = True
