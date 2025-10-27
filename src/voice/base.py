"""语音服务提供商抽象基类"""

from abc import ABCMeta, abstractmethod
from typing import Optional
from io import BytesIO


class _AbstractVoiceProvider(metaclass=ABCMeta):
    """语音提供商接口基类
    
    作用是提供统一的抽象父类,强制子类实现必要的方法,避免遗漏实现导致运行时异常。
    """

    @abstractmethod
    async def close(self):
        """关闭并清理资源"""
        raise NotImplementedError("子类必须实现 close() 方法")


class TTSProvider(_AbstractVoiceProvider):
    """文本转语音(TTS)提供商抽象基类
    
    所有 TTS 提供商必须继承此类并实现 synthesize() 和 close() 方法。
    """

    @abstractmethod
    async def synthesize(self, text: str) -> Optional[BytesIO]:
        """将文本转换为语音音频
        
        Args:
            text: 要转换的文本
            
        Returns:
            包含音频数据的 BytesIO 对象,失败返回 None
        """
        raise NotImplementedError("子类必须实现 synthesize() 方法")

    @abstractmethod
    async def close(self):
        """关闭并清理资源"""
        raise NotImplementedError("子类必须实现 close() 方法")


class STTProvider(_AbstractVoiceProvider):
    """语音转文本(STT)提供商抽象基类
    
    所有 STT 提供商必须继承此类并实现 recognize() 和 close() 方法。
    """

    @abstractmethod
    async def recognize(self, audio_data: bytes) -> Optional[str]:
        """识别语音并转换为文本
        
        Args:
            audio_data: 音频数据字节流 (PCM 格式)
            
        Returns:
            识别出的文本,失败返回 None
        """
        raise NotImplementedError("子类必须实现 recognize() 方法")

    @abstractmethod
    async def close(self):
        """关闭并清理资源"""
        raise NotImplementedError("子类必须实现 close() 方法")
