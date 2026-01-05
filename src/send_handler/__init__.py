"""包名称：send_handler
功能说明：提供 Discord 消息发送相关的工具类。"""

from .main_send_handler import DiscordSendHandler, send_handler, get_send_handler

__all__ = ["DiscordSendHandler", "send_handler", "get_send_handler"]
