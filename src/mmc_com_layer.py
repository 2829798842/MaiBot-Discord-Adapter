"""模块名称：MaiBot 通信层
主要功能：管理与 MaiBot Core 的 WebSocket 连接和消息路由
"""

from typing import Optional
from maim_message import Router, RouteConfig, TargetConfig
from .config import global_config
from .logger import logger, custom_logger

# 延迟初始化 - 在配置注入后才创建实际实例
_router_instance: Optional[Router] = None


def _create_router() -> Router:
    """创建路由器实例（延迟初始化）"""
    route_config = RouteConfig(
        route_config={
            global_config.maibot_server.platform_name: TargetConfig(
                url=f"ws://{global_config.maibot_server.host}:{global_config.maibot_server.port}/ws",
                token=global_config.maibot_server.token,
            )
        }
    )
    return Router(route_config, custom_logger)


def get_router() -> Router:
    """获取路由器实例，如果不存在则创建"""
    global _router_instance
    if _router_instance is None:
        logger.info("正在创建 MaiBot Core 路由器实例...")
        _router_instance = _create_router()
    return _router_instance


def reset_router():
    """重置路由器实例（用于重新初始化）"""
    global _router_instance
    _router_instance = None


# 为了保持向后兼容，提供代理对象
class _RouterProxy:
    """路由器代理类，实现延迟初始化"""
    def __getattr__(self, name):
        return getattr(get_router(), name)


router = _RouterProxy()


async def mmc_start_com():
    """启动与 MaiBot Core 的通信连接"""
    logger.debug("正在连接 MaiBot Core...")
    # 确保使用最新配置创建路由器
    actual_router = get_router()
    # 消息处理器会在 main.py 中注册
    await actual_router.run()


async def mmc_stop_com():
    """停止与 MaiBot Core 的通信连接"""
    logger.debug("正在关闭与 MaiBot Core 的连接...")
    actual_router = get_router()
    await actual_router.stop()
