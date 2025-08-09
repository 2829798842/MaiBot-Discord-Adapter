"""包名称：MaiBot Discord 适配器源码包
功能说明：Discord 适配器的核心功能实现模块
"""

__version__ = "1.0.0"
__author__ = "Yang208115"

# 模块导入
from .logger import logger
from .config import global_config
from .mmc_com_layer import router, mmc_start_com, mmc_stop_com

__all__ = [
    "logger",
    "global_config", 
    "router",
    "mmc_start_com",
    "mmc_stop_com",
]