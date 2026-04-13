"""Discord 语音功能模块。"""

from .base import STTProvider, TTSProvider
from .voice_manager import VoiceManager

__all__ = ["TTSProvider", "STTProvider", "VoiceManager"]
