"""语音功能配置模块

定义各语音服务提供商的配置类
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class AzureVoiceConfig:
    """Azure 语音服务配置
    
    Attributes:
        subscription_key: Azure 订阅密钥
        region: Azure 服务区域（如 eastasia, westus）
        tts_voice: TTS 语音名称
        stt_language: STT 识别语言
    """
    subscription_key: str = ""
    region: str = "eastasia"
    tts_voice: str = "zh-CN-XiaoxiaoNeural"
    stt_language: str = "zh-CN"


@dataclass
class AliyunVoiceConfig:
    """阿里云语音服务配置（占位）
    
    Attributes:
        access_key_id: 阿里云 AccessKey ID
        access_key_secret: 阿里云 AccessKey Secret
        app_key: 应用 AppKey
    """
    access_key_id: str = ""
    access_key_secret: str = ""
    app_key: str = ""


@dataclass
class AIHobbyistVoiceConfig:
    """AI Hobbyist TTS 配置 (GPT-SoVITS v4)
    
    Attributes:
        api_base: API 基础地址
        api_token: API 访问令牌（从 https://gsv.acgnai.top 获取）
        model_name: 默认语音模型
        language: 默认语言
        emotion: 默认语气
    """
    api_base: str = "https://gsv2p.acgnai.top"
    api_token: Optional[str] = None
    model_name: str = "崩坏三-中文-爱莉希雅_ZH"
    language: str = "中文"
    emotion: str = "默认"


@dataclass
class SiliconFlowVoiceConfig:
    """SiliconFlow 语音服务配置
    
    Attributes:
        api_key: SiliconFlow API 密钥
        api_base: API 基础地址
        tts_model: TTS 模型名称
        tts_voice: TTS 语音音色
        stt_model: STT 模型名称
        response_format: 音频输出格式
        sample_rate: 采样率
        speed: 语速（0.25-4.0）
    """
    api_key: str = ""
    api_base: str = "https://api.siliconflow.cn/v1"
    tts_model: str = "fnlp/MOSS-TTSD-v0.5"
    tts_voice: str = "fnlp/MOSS-TTSD-v0.5:alex"
    stt_model: str = "FunAudioLLM/SenseVoiceSmall"
    response_format: str = "pcm"
    sample_rate: int = 48000
    speed: float = 1.0


@dataclass
class VoiceConfig:
    """语音功能总配置
    
    Attributes:
        enabled: 是否启用语音功能
        voice_channel_whitelist: 语音频道白名单
        check_interval: 频道切换检查间隔（秒），仅多频道时生效
        tts_provider: TTS 提供商（azure/ai_hobbyist/siliconflow）
        stt_provider: STT 提供商（azure/aliyun/siliconflow）
        azure: Azure 配置
        aliyun: 阿里云配置
        ai_hobbyist: AI Hobbyist TTS 配置
        siliconflow: SiliconFlow 配置
    """
    enabled: bool = False
    voice_channel_whitelist: list = None
    check_interval: int = 10
    tts_provider: str = "azure"
    stt_provider: str = "azure"
    azure: AzureVoiceConfig = None
    aliyun: AliyunVoiceConfig = None
    ai_hobbyist: AIHobbyistVoiceConfig = None
    siliconflow: SiliconFlowVoiceConfig = None

    def __post_init__(self):
        if self.voice_channel_whitelist is None:
            self.voice_channel_whitelist = []
        if self.azure is None:
            self.azure = AzureVoiceConfig()
        if self.aliyun is None:
            self.aliyun = AliyunVoiceConfig()
        if self.ai_hobbyist is None:
            self.ai_hobbyist = AIHobbyistVoiceConfig()
        if self.siliconflow is None:
            self.siliconflow = SiliconFlowVoiceConfig()
