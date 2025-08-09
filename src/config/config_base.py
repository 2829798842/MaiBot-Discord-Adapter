"""模块名称：配置基础类
主要功能：定义配置数据类的基础结构
"""

from dataclasses import dataclass
from typing import Optional, List, Dict, Any


@dataclass
class ChatConfig:
    """聊天控制配置类
    
    Attributes:
        guild_list_type (str): 服务器名单类型（whitelist, blacklist）
        guild_list (List[int]): 服务器名单
        channel_list_type (str): 频道名单类型（whitelist, blacklist）
        channel_list (List[int]): 频道名单
        user_list_type (str): 用户名单类型（whitelist, blacklist）
        user_list (List[int]): 用户名单
    """
    
    guild_list_type: str = "whitelist"
    guild_list: List[int] = None
    channel_list_type: str = "whitelist"
    channel_list: List[int] = None
    user_list_type: str = "whitelist"
    user_list: List[int] = None
    
    def __post_init__(self):
        if self.guild_list is None:
            self.guild_list = []
        if self.channel_list is None:
            self.channel_list = []
        if self.user_list is None:
            self.user_list = []


@dataclass
class DiscordConfig:
    """Discord Bot 配置类
    
    Attributes:
        token (str): Discord Bot Token
        intents (Dict[str, bool]): Discord 权限意图配置
    """
    
    token: str = ""
    intents: Dict[str, bool] = None
    
    def __post_init__(self):
        if self.intents is None:
            self.intents = {}


@dataclass
class MaiBotServerConfig:
    """MaiBot 服务器配置类
    
    Attributes:
        host (str): MaiBot Core 主机地址
        port (int): MaiBot Core 端口
        platform_name (str): 平台标识符
        token (Optional[str]): 认证 Token
    """
    
    host: str = "127.0.0.1"
    port: int = 8000
    platform_name: str = "discord_bot_instance_1"
    token: Optional[str] = None


@dataclass
class DebugConfig:
    """调试配置类
    
    Attributes:
        level (str): 日志级别
        log_file (Optional[str]): 日志文件路径
    """
    
    level: str = "INFO"
    log_file: Optional[str] = "logs/discord_adapter.log"


@dataclass
class GlobalConfig:
    """全局配置类
    
    Attributes:
        discord (DiscordConfig): Discord 配置
        chat (ChatConfig): 聊天控制配置
        maibot_server (MaiBotServerConfig): MaiBot 服务器配置
        debug (DebugConfig): 调试配置
    """
    
    discord: DiscordConfig = None
    chat: ChatConfig = None
    maibot_server: MaiBotServerConfig = None  
    debug: DebugConfig = None
    
    def __post_init__(self):
        if self.discord is None:
            self.discord = DiscordConfig()
        if self.chat is None:
            self.chat = ChatConfig()
        if self.maibot_server is None:
            self.maibot_server = MaiBotServerConfig()
        if self.debug is None:
            self.debug = DebugConfig()