"""Azure TTS 提供商实现"""

from typing import Optional
from io import BytesIO
import asyncio
import azure.cognitiveservices.speech as speechsdk
from src.voice.base import TTSProvider
from src.logger import logger as base_logger

logger = base_logger.bind(name="AzureTTS")


class AzureTTSProvider(TTSProvider):
    """Azure TTS 提供商"""

    def __init__(self, config):
        self.config = config
        self.speech_config = speechsdk.SpeechConfig(
            subscription=config.subscription_key,
            region=config.region
        )

        if getattr(config, 'tts_voice', None):
            self.speech_config.speech_synthesis_voice_name = config.tts_voice

        self.speech_config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Raw48Khz16BitMonoPcm
        )

        self.synthesizer = speechsdk.SpeechSynthesizer(
            speech_config=self.speech_config,
            audio_config=None
        )

        logger.info(f"Azure TTS 初始化完成 [语音: {config.tts_voice}]")

    async def synthesize(self, text: str) -> Optional[BytesIO]:
        """合成语音"""
        if not text:
            return None

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self.synthesizer.speak_text(text)
            )

            if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                logger.debug(f"TTS 合成成功: {text[:50]}...")
                return BytesIO(result.audio_data)
            logger.error(f"Azure TTS 合成失败: {result.reason}")
            if result.reason == speechsdk.ResultReason.Canceled:
                cancellation = result.cancellation_details
                logger.error(f"错误详情: {cancellation.reason} - {cancellation.error_details}")
            return None

        except Exception as e:  # pylint: disable=broad-except
            logger.error(f"Azure TTS 异常: {e}")
            return None

    async def close(self):
        """关闭资源"""
        logger.info("Azure TTS 提供商已关闭")
