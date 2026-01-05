"""模块名称：配置管理器
主要功能：加载和管理应用程序配置
"""

import os
import logging
from pathlib import Path
import tomlkit
from .config_base import GlobalConfig, DiscordConfig, ChatConfig, MaiBotServerConfig, DebugConfig
from .voice_config import (
    VoiceConfig,
    AzureVoiceConfig,
    AliyunVoiceConfig,
    AIHobbyistVoiceConfig,
    SiliconFlowVoiceConfig,
)

from typing import Optional

logger = logging.getLogger(__name__)

# 获取插件目录路径 (config.py 所在目录的上上级)
_PLUGIN_DIR = Path(__file__).parent.parent.parent
_DEFAULT_CONFIG_PATH = _PLUGIN_DIR / "config.toml"


def load_config(config_path: Optional[str] = None) -> GlobalConfig:
    """加载配置文件

    Args:
        config_path: 配置文件路径，默认为插件目录下的 config.toml

    Returns:
        GlobalConfig: 加载的全局配置对象
    """
    config = GlobalConfig()

    # 如果未指定路径，使用插件目录下的 config.toml
    if config_path is None:
        config_path = str(_DEFAULT_CONFIG_PATH)

    if not os.path.exists(config_path):
        print(f"警告: 配置文件 {config_path} 不存在，使用默认配置")
        return config

    try:
        with open(config_path, "r", encoding="utf-8") as file:
            config_data = tomlkit.load(file)

        # 加载 Discord 配置
        discord_config = config_data.get("discord", {})

        config.discord = DiscordConfig(
            token=discord_config.get("token", ""),
            intents=discord_config.get("intents", {}),
            retry=discord_config.get("retry", {}),
        )

        # 加载聊天控制配置
        chat_config = config_data.get("chat", {})
        config.chat = ChatConfig(
            guild_list_type=chat_config.get("guild_list_type", "whitelist"),
            guild_list=chat_config.get("guild_list", []),
            channel_list_type=chat_config.get("channel_list_type", "whitelist"),
            channel_list=chat_config.get("channel_list", []),
            thread_list_type=chat_config.get("thread_list_type", "whitelist"),
            thread_list=chat_config.get("thread_list", []),
            user_list_type=chat_config.get("user_list_type", "whitelist"),
            user_list=chat_config.get("user_list", []),
            allow_thread_interaction=chat_config.get("allow_thread_interaction", True),
            inherit_channel_permissions=chat_config.get("inherit_channel_permissions", True),
            inherit_channel_memory=chat_config.get("inherit_channel_memory", True),
        )

        # 加载 MaiBot 服务器配置
        maibot_config = config_data.get("maibot_server", {})
        config.maibot_server = MaiBotServerConfig(
            host=maibot_config.get("host", "127.0.0.1"),
            port=maibot_config.get("port", 8000),
            platform_name=maibot_config.get("platform_name", "discord_bot_instance_1"),
            token=maibot_config.get("token"),
        )

        # 加载调试配置
        debug_config = config_data.get("debug", {})
        config.debug = DebugConfig(
            level=debug_config.get("level", "INFO"), log_file=debug_config.get("log_file", "logs/discord_adapter.log")
        )

        # 加载语音配置
        voice_config_data = config_data.get("voice", {})
        if voice_config_data:
            try:
                # 加载 Azure 配置
                azure_data = voice_config_data.get("azure", {})
                azure_cfg = AzureVoiceConfig(
                    subscription_key=azure_data.get("subscription_key", ""),
                    region=azure_data.get("region", "eastasia"),
                    tts_voice=azure_data.get("tts_voice", "zh-CN-XiaoxiaoNeural"),
                    stt_language=azure_data.get("stt_language", "zh-CN"),
                )

                # 加载阿里云配置
                aliyun_data = voice_config_data.get("aliyun", {})
                aliyun_cfg = AliyunVoiceConfig(
                    access_key_id=aliyun_data.get("access_key_id", ""),
                    access_key_secret=aliyun_data.get("access_key_secret", ""),
                    app_key=aliyun_data.get("app_key", ""),
                )

                # 加载 AI Hobbyist TTS 配置
                ai_hobbyist_data = voice_config_data.get("ai_hobbyist", {})

                ai_hobbyist_cfg = AIHobbyistVoiceConfig(
                    api_base=ai_hobbyist_data.get("api_base", "https://gsv2p.acgnai.top"),
                    api_token=ai_hobbyist_data.get("api_token"),
                    model_name=ai_hobbyist_data.get("model_name", "崩坏三-中文-爱莉希雅_ZH"),
                    language=ai_hobbyist_data.get("language", "中文"),
                    emotion=ai_hobbyist_data.get("emotion", "默认"),
                )

                # 加载 SiliconFlow 配置
                siliconflow_data = voice_config_data.get("siliconflow", {})
                siliconflow_cfg = SiliconFlowVoiceConfig(
                    api_key=siliconflow_data.get("api_key", ""),
                    api_base=siliconflow_data.get("api_base", "https://api.siliconflow.cn/v1"),
                    tts_model=siliconflow_data.get("tts_model", "fnlp/MOSS-TTSD-v0.5"),
                    tts_voice=siliconflow_data.get("tts_voice", "fnlp/MOSS-TTSD-v0.5:alex"),
                    stt_model=siliconflow_data.get("stt_model", "FunAudioLLM/SenseVoiceSmall"),
                    response_format=siliconflow_data.get("response_format", "pcm"),
                    sample_rate=siliconflow_data.get("sample_rate", 48000),
                    speed=siliconflow_data.get("speed", 1.0),
                )

                # 创建 VoiceConfig
                raw_tts_provider = voice_config_data.get("tts_provider", "azure")

                config.voice = VoiceConfig(
                    enabled=voice_config_data.get("enabled", False),
                    voice_channel_whitelist=voice_config_data.get("voice_channel_whitelist", []),
                    check_interval=voice_config_data.get("check_interval", 10),
                    tts_provider=raw_tts_provider,
                    stt_provider=voice_config_data.get("stt_provider", "azure"),
                    azure=azure_cfg,
                    aliyun=aliyun_cfg,
                    ai_hobbyist=ai_hobbyist_cfg,
                    siliconflow=siliconflow_cfg,
                )
            except (KeyError, TypeError, ValueError) as e:
                # 忽略 voice 配置解析错误，保持默认
                logger.warning(f"加载语音配置失败，使用默认配置: {e}")  # pylint: disable=logging-fstring-interpolation

        return config
    except (FileNotFoundError, TypeError) as e:
        print(f"加载配置文件失败: {e}，使用默认配置")
        return GlobalConfig()


def inject_plugin_config(plugin_config: dict) -> None:
    """从插件配置注入到 global_config
    
    Args:
        plugin_config: 插件配置字典（来自 MaiBot 插件系统）
    """
    global global_config
    
    if not plugin_config:
        logger.warning("插件配置为空，跳过注入")
        return
    
    # Discord 配置
    discord_cfg = plugin_config.get("discord", {})
    global_config.discord.token = discord_cfg.get("token", "")
    
    # intents 配置（嵌套在 discord.intents 下）
    intents_cfg = plugin_config.get("discord.intents", plugin_config.get("discord", {}).get("intents", {}))
    if intents_cfg:
        global_config.discord.intents = {
            "messages": intents_cfg.get("messages", True),
            "guilds": intents_cfg.get("guilds", True),
            "dm_messages": intents_cfg.get("dm_messages", True),
            "message_content": intents_cfg.get("message_content", True),
            "voice_states": intents_cfg.get("voice_states", False),
            "reactions": True,
        }
    
    # retry 配置
    retry_cfg = plugin_config.get("discord.retry", plugin_config.get("discord", {}).get("retry", {}))
    if retry_cfg:
        global_config.discord.retry = {
            "retry_delay": retry_cfg.get("retry_delay", 5),
            "connection_check_interval": retry_cfg.get("connection_check_interval", 30),
        }
    
    # Chat 配置
    chat_cfg = plugin_config.get("chat", {})
    if chat_cfg:
        global_config.chat.guild_list_type = chat_cfg.get("guild_list_type", "blacklist")
        global_config.chat.guild_list = chat_cfg.get("guild_list", [])
        global_config.chat.channel_list_type = chat_cfg.get("channel_list_type", "blacklist")
        global_config.chat.channel_list = chat_cfg.get("channel_list", [])
        global_config.chat.thread_list_type = chat_cfg.get("thread_list_type", "blacklist")
        global_config.chat.thread_list = chat_cfg.get("thread_list", [])
        global_config.chat.user_list_type = chat_cfg.get("user_list_type", "blacklist")
        global_config.chat.user_list = chat_cfg.get("user_list", [])
        global_config.chat.allow_thread_interaction = chat_cfg.get("allow_thread_interaction", True)
        global_config.chat.inherit_channel_permissions = chat_cfg.get("inherit_channel_permissions", True)
        global_config.chat.inherit_channel_memory = chat_cfg.get("inherit_channel_memory", True)
    
    # MaiBot 服务器配置
    maibot_cfg = plugin_config.get("maibot_server", {})
    if maibot_cfg:
        global_config.maibot_server.host = maibot_cfg.get("host", "127.0.0.1")
        global_config.maibot_server.port = maibot_cfg.get("port", 8000)
        global_config.maibot_server.platform_name = maibot_cfg.get("platform_name", "discord_bot_instance_1")
    
    # Debug 配置
    debug_cfg = plugin_config.get("debug", {})
    if debug_cfg:
        global_config.debug.level = debug_cfg.get("level", "INFO")
        global_config.debug.log_file = debug_cfg.get("log_file", "logs/discord_adapter.log")
    
    # Voice 配置
    voice_cfg = plugin_config.get("voice", {})
    if voice_cfg:
        global_config.voice.enabled = voice_cfg.get("enabled", False)
        global_config.voice.voice_channel_whitelist = voice_cfg.get("voice_channel_whitelist", [])
        global_config.voice.check_interval = voice_cfg.get("check_interval", 30)
        global_config.voice.tts_provider = voice_cfg.get("tts_provider", "azure")
        global_config.voice.stt_provider = voice_cfg.get("stt_provider", "azure")
        
        # Azure voice 配置
        azure_cfg = plugin_config.get("voice.azure", {})
        if azure_cfg:
            global_config.voice.azure.subscription_key = azure_cfg.get("subscription_key", "")
            global_config.voice.azure.region = azure_cfg.get("region", "eastasia")
            global_config.voice.azure.tts_voice = azure_cfg.get("tts_voice", "zh-CN-XiaoxiaoNeural")
            global_config.voice.azure.stt_language = azure_cfg.get("stt_language", "zh-CN")
        
        # Aliyun voice 配置
        aliyun_cfg = plugin_config.get("voice.aliyun", {})
        if aliyun_cfg:
            global_config.voice.aliyun.access_key_id = aliyun_cfg.get("access_key_id", "")
            global_config.voice.aliyun.access_key_secret = aliyun_cfg.get("access_key_secret", "")
            global_config.voice.aliyun.app_key = aliyun_cfg.get("app_key", "")
        
        # AI Hobbyist voice 配置
        ai_hobbyist_cfg = plugin_config.get("voice.ai_hobbyist", {})
        if ai_hobbyist_cfg:
            global_config.voice.ai_hobbyist.api_base = ai_hobbyist_cfg.get("api_base", "https://gsv2p.acgnai.top")
            global_config.voice.ai_hobbyist.api_token = ai_hobbyist_cfg.get("api_token", "")
            global_config.voice.ai_hobbyist.model_name = ai_hobbyist_cfg.get("model_name", "原神_中文_芙宁娜_ZH")
            global_config.voice.ai_hobbyist.language = ai_hobbyist_cfg.get("language", "中文")
            global_config.voice.ai_hobbyist.emotion = ai_hobbyist_cfg.get("emotion", "默认")
        
        # SiliconFlow voice 配置
        siliconflow_cfg = plugin_config.get("voice.siliconflow", {})
        if siliconflow_cfg:
            global_config.voice.siliconflow.api_key = siliconflow_cfg.get("api_key", "")
            global_config.voice.siliconflow.api_base = siliconflow_cfg.get("api_base", "https://api.siliconflow.cn/v1")
            global_config.voice.siliconflow.tts_model = siliconflow_cfg.get("tts_model", "fnlp/MOSS-TTSD-v0.5")
            global_config.voice.siliconflow.tts_voice = siliconflow_cfg.get("tts_voice", "fnlp/MOSS-TTSD-v0.5:alex")
            global_config.voice.siliconflow.stt_model = siliconflow_cfg.get("stt_model", "FunAudioLLM/SenseVoiceSmall")
            global_config.voice.siliconflow.response_format = siliconflow_cfg.get("response_format", "pcm")
            global_config.voice.siliconflow.sample_rate = siliconflow_cfg.get("sample_rate", 44100)
            global_config.voice.siliconflow.speed = siliconflow_cfg.get("speed", 1.0)
    
    logger.info(f"配置注入完成: token={'*'*8 if global_config.discord.token else '(未设置)'}, platform={global_config.maibot_server.platform_name}")


def is_user_allowed(
    config: GlobalConfig,
    user_id: int,
    guild_id: int = None,
    channel_id: int = None,
    thread_id: int = None,
    is_thread: bool = False,
) -> bool:
    """检查用户是否被允许使用

    Args:
        config: 全局配置对象
        user_id: 用户 ID
        guild_id: 服务器 ID（可选）
        channel_id: 频道 ID（可选）
        thread_id: 子区 ID（可选）
        is_thread: 是否为子区消息

    Returns:
        bool: 是否允许该用户使用
    """
    chat_config = config.chat
    logger.debug(
        "权限检查开始: 用户={user_id}, 服务器={guild_id}, 频道={channel_id}, 子区={thread_id}, 是否子区={is_thread}"
    )

    # 如果是子区消息且全局禁用子区交互
    if is_thread and not chat_config.allow_thread_interaction:
        logger.debug("子区交互已被全局禁用")
        return False

    # 检查用户权限
    logger.debug("用户权限检查: 类型={chat_config.user_list_type}, 列表={chat_config.user_list}")
    if chat_config.user_list_type == "whitelist":
        if user_id not in chat_config.user_list:
            logger.debug("用户 {user_id} 不在白名单中")
            return False
    elif chat_config.user_list_type == "blacklist":
        if user_id in chat_config.user_list:
            logger.debug("用户 {user_id} 在黑名单中")
            return False

    # 检查服务器权限（如果是服务器消息）
    if guild_id is not None:
        logger.debug("服务器权限检查: 类型={chat_config.guild_list_type} 列表={chat_config.guild_list}")
        if chat_config.guild_list_type == "whitelist":
            if guild_id not in chat_config.guild_list:
                logger.debug("服务器 {guild_id} 不在白名单中")
                return False
        elif chat_config.guild_list_type == "blacklist":
            if guild_id in chat_config.guild_list:
                logger.debug("服务器 {guild_id} 在黑名单中")
                return False

    # 检查频道权限（如果是频道消息）
    if channel_id is not None:
        logger.debug("频道权限检查: 类型={chat_config.channel_list_type}, 列表={chat_config.channel_list}")
        if chat_config.channel_list_type == "whitelist":
            if channel_id not in chat_config.channel_list:
                logger.debug("频道 {channel_id} 不在白名单中")
                return False
        elif chat_config.channel_list_type == "blacklist":
            if channel_id in chat_config.channel_list:
                logger.debug("频道 {channel_id} 在黑名单中")
                return False

    # 检查子区权限（如果是子区消息）
    if is_thread and thread_id is not None:
        # 如果启用了继承父频道权限，则跳过子区独立权限检查
        if not chat_config.inherit_channel_permissions:
            logger.debug("子区权限检查: 类型={chat_config.thread_list_type}, 列表={chat_config.thread_list}")
            if chat_config.thread_list_type == "whitelist":
                if thread_id not in chat_config.thread_list:
                    logger.debug("子区 {thread_id} 不在白名单中")
                    return False
            elif chat_config.thread_list_type == "blacklist":
                if thread_id in chat_config.thread_list:
                    logger.debug("子区 {thread_id} 在黑名单中")
                    return False
        else:
            logger.debug("子区继承父频道权限，跳过独立权限检查")

    logger.debug("权限检查通过")
    return True


# 惰性初始化：先创建默认配置，由插件系统通过 inject_plugin_config() 注入实际配置
global_config = GlobalConfig()
