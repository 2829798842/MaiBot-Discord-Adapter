"""消息接收处理器模块初始化"""

from .discord_client import discord_client
from .message_handler import message_handler

__all__ = ["discord_client", "message_handler"]