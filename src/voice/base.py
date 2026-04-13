"""语音服务提供商抽象基类。"""

from abc import ABCMeta, abstractmethod
from io import BytesIO
from typing import Optional


class _AbstractVoiceProvider(metaclass=ABCMeta):
    """语音提供商接口基类。"""

    @abstractmethod
    async def close(self) -> None:
        """释放提供商占用的资源（如 HTTP 会话、连接等）。

        Returns:
            None
        """
        raise NotImplementedError

class TTSProvider(_AbstractVoiceProvider):
    """文本转语音提供商抽象基类。"""

    @abstractmethod
    async def synthesize(self, text: str) -> Optional[BytesIO]:
        """将文本合成为音频流。

        Args:
            text: 待朗读的文本内容。

        Returns:
            成功时返回包含音频字节的内存流；失败或无可合成内容时返回 None。
        """
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """关闭 TTS 提供商并释放相关资源。

        Returns:
            None
        """
        raise NotImplementedError


class STTProvider(_AbstractVoiceProvider):
    """语音转文本提供商抽象基类。"""

    @abstractmethod
    async def recognize(self, audio_data: bytes) -> Optional[str]:
        """对音频数据进行语音识别。

        Args:
            audio_data: 原始音频字节（通常为 PCM 等格式，由具体实现约定）。

        Returns:
            识别到的文本；无有效结果或失败时返回 None。
        """
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """关闭 STT 提供商并释放相关资源。

        Returns:
            None
        """
        raise NotImplementedError
