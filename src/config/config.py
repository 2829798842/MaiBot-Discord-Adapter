"""模块名称：配置管理器
主要功能：加载和管理应用程序配置
"""

import toml
import os
from .config_base import GlobalConfig, DiscordConfig, ChatConfig, MaiBotServerConfig, DebugConfig

def load_config(config_path: str = "config.toml") -> GlobalConfig:
    """加载配置文件
    
    Args:
        config_path: 配置文件路径
        
    Returns:
        GlobalConfig: 加载的全局配置对象
    """
    config = GlobalConfig()
    
    if not os.path.exists(config_path):
        print(f"警告: 配置文件 {config_path} 不存在，使用默认配置")
        return config
    
    try:
        with open(config_path, 'r', encoding='utf-8') as file:
            config_data = toml.load(file)
        
        # 加载 Discord 配置
        discord_config = config_data.get('discord', {})
        config.discord = DiscordConfig(
            token=discord_config.get('token', ''),
            intents=discord_config.get('intents', {})
        )
        
        # 加载聊天控制配置
        chat_config = config_data.get('chat', {})
        config.chat = ChatConfig(
            guild_list_type=chat_config.get('guild_list_type', 'whitelist'),
            guild_list=chat_config.get('guild_list', []),
            channel_list_type=chat_config.get('channel_list_type', 'whitelist'),
            channel_list=chat_config.get('channel_list', []),
            user_list_type=chat_config.get('user_list_type', 'whitelist'),
            user_list=chat_config.get('user_list', [])
        )
        
        # 加载 MaiBot 服务器配置
        maibot_config = config_data.get('maibot_server', {})
        config.maibot_server = MaiBotServerConfig(
            host=maibot_config.get('host', '127.0.0.1'),
            port=maibot_config.get('port', 8000),
            platform_name=maibot_config.get('platform_name', 'discord_bot_instance_1'),
            token=maibot_config.get('token')
        )
        
        # 加载调试配置
        debug_config = config_data.get('debug', {})
        config.debug = DebugConfig(
            level=debug_config.get('level', 'INFO'),
            log_file=debug_config.get('log_file', 'logs/discord_adapter.log')
        )
        
        # 验证必要配置
        if not config.discord.token or config.discord.token == "YOUR_DISCORD_BOT_TOKEN_HERE":
            raise ValueError("请在配置文件中设置有效的 Discord Bot Token")
            
        return config
        
    except Exception as e:
        print(f"加载配置文件失败: {e}")
        raise


def is_user_allowed(config: GlobalConfig, user_id: int, guild_id: int = None, channel_id: int = None) -> bool:
    """检查用户是否被允许使用
    
    Args:
        config: 全局配置对象
        user_id: 用户 ID
        guild_id: 服务器 ID（可选）
        channel_id: 频道 ID（可选）
        
    Returns:
        bool: 是否允许该用户使用
    """
    import logging
    logger = logging.getLogger(__name__)
    
    chat_config = config.chat
    logger.debug(f"权限检查开始: 用户={user_id}, 服务器={guild_id}, 频道={channel_id}")
    
    # 检查用户权限
    logger.debug(f"用户权限检查: 类型={chat_config.user_list_type}, 列表={chat_config.user_list}")
    if chat_config.user_list_type == "whitelist":
        # 白名单模式：只有在用户名单中的用户可以使用
        if user_id not in chat_config.user_list:
            logger.debug(f"用户 {user_id} 不在白名单中")
            return False
    elif chat_config.user_list_type == "blacklist":
        # 黑名单模式：用户名单中的用户禁止使用
        if user_id in chat_config.user_list:
            logger.debug(f"用户 {user_id} 在黑名单中")
            return False
    
    # 检查服务器权限（如果是服务器消息）
    if guild_id is not None:
        logger.debug(f"服务器权限检查: 类型={chat_config.guild_list_type}, 列表={chat_config.guild_list}")
        if chat_config.guild_list_type == "whitelist":
            # 白名单模式：只有在服务器名单中的服务器可以使用
            if guild_id not in chat_config.guild_list:
                logger.debug(f"服务器 {guild_id} 不在白名单中")
                return False
        elif chat_config.guild_list_type == "blacklist":
            # 黑名单模式：服务器名单中的服务器禁止使用
            if guild_id in chat_config.guild_list:
                logger.debug(f"服务器 {guild_id} 在黑名单中")
                return False
    
    # 检查频道权限（如果是频道消息）
    if channel_id is not None:
        logger.debug(f"频道权限检查: 类型={chat_config.channel_list_type}, 列表={chat_config.channel_list}")
        if chat_config.channel_list_type == "whitelist":
            # 白名单模式：只有在频道名单中的频道可以使用
            if channel_id not in chat_config.channel_list:
                logger.debug(f"频道 {channel_id} 不在白名单中")
                return False
        elif chat_config.channel_list_type == "blacklist":
            # 黑名单模式：频道名单中的频道禁止使用
            if channel_id in chat_config.channel_list:
                logger.debug(f"频道 {channel_id} 在黑名单中")
                return False
    
    logger.debug("权限检查通过")
    return True


# 加载全局配置
global_config = load_config()