"""模块名称：MaiBot 通信层
主要功能：管理与 MaiBot Core 的 WebSocket 连接和消息路由
"""

from maim_message import Router, RouteConfig, TargetConfig
from .config import global_config
from .logger import logger, custom_logger

# 创建路由配置
route_config = RouteConfig(
    route_config={
        global_config.maibot_server.platform_name: TargetConfig(
            url=f"ws://{global_config.maibot_server.host}:{global_config.maibot_server.port}/ws",
            token=global_config.maibot_server.token,
        )
    }
)

# 创建路由器实例
router = Router(route_config, custom_logger)


async def mmc_start_com():
    """启动与 MaiBot Core 的通信连接"""
    logger.debug("正在连接 MaiBot Core...")
    # 消息处理器会在 main.py 中注册
    await router.run()


async def mmc_stop_com():
    """停止与 MaiBot Core 的通信连接"""
    logger.debug("正在关闭与 MaiBot Core 的连接...")
    await router.stop()