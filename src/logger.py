"""模块名称：日志管理器
主要功能：配置和管理整个适配器的日志系统
"""

from loguru import logger
from .config import global_config
import sys

# 默认 logger
logger.remove()
logger.add(
    sys.stderr,
    level=global_config.debug.level,
    format="<blue>{time:YYYY-MM-DD HH:mm:ss}</blue> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    filter=lambda record: "name" not in record["extra"] or record["extra"].get("name") != "maim_message",
)

logger.add(
    sys.stderr,
    level="INFO",
    format="<red>{time:YYYY-MM-DD HH:mm:ss}</red> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    filter=lambda record: record["extra"].get("name") == "maim_message",
)

# 文件日志
if global_config.debug.log_file:
    logger.add(
        global_config.debug.log_file,
        level=global_config.debug.level,
        rotation="10 MB",
        retention="7 days",
        compression="zip",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    )

# 创建样式不同的 logger
custom_logger = logger.bind(name="maim_message")
logger = logger.bind(name="MaiBot-Discord-Adapter")