"""Azure STT 提供商实现"""

from typing import Optional
import asyncio
import azure.cognitiveservices.speech as speechsdk
from src.voice.base import STTProvider
from src.logger import logger as base_logger

logger = base_logger.bind(name="AzureSTT")


class AzureSTTProvider(STTProvider):
    """Azure STT 提供商"""

    def __init__(self, config):
        self.config = config
        self.speech_config = speechsdk.SpeechConfig(
            subscription=config.subscription_key,
            region=config.region
        )

        if getattr(config, 'stt_language', None):
            self.speech_config.speech_recognition_language = config.stt_language

        logger.info(f"Azure STT 初始化完成 [语言: {config.stt_language}]")

    async def recognize(self, audio_data: bytes) -> Optional[str]:
        """识别语音"""
        if not audio_data:
            return None

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self._recognize_sync(audio_data)
            )
            return result

        except Exception as e:  # pylint: disable=broad-except
            logger.error(f"Azure STT 识别失败: {e}")
            return None

    def _recognize_sync(self, audio_data: bytes) -> Optional[str]:
        """同步识别语音（在 executor 中运行）"""
        try:
            stream = speechsdk.audio.PushAudioInputStream()
            audio_config = speechsdk.audio.AudioConfig(stream=stream)

            recognizer = speechsdk.SpeechRecognizer(
                speech_config=self.speech_config,
                audio_config=audio_config
            )

            stream.write(audio_data)
            stream.close()

            result = recognizer.recognize_once()

            if result.reason == speechsdk.ResultReason.RecognizedSpeech:
                logger.debug(f"识别结果: {result.text}")
                return result.text
            if result.reason == speechsdk.ResultReason.NoMatch:
                logger.debug("未识别到语音")
                return None
            if result.reason == speechsdk.ResultReason.Canceled:
                cancellation = result.cancellation_details
                logger.error(f"识别被取消: {cancellation.reason} - {cancellation.error_details}")
                return None
            logger.warning(f"识别结果异常: {result.reason}")
            return None

        except Exception as e:  # pylint: disable=broad-except
            logger.error(f"同步识别异常: {e}")
            return None

    async def close(self):
        """关闭资源"""
        logger.info("Azure STT 提供商已关闭")
