"""模块名称：工具函数
主要功能：提供通用的工具函数和辅助方法
"""

import asyncio
import time
from typing import Any, List
from pathlib import Path


def ensure_directory(path: str) -> None:
    """确保目录存在，如果不存在则创建
    
    Args:
        path: 目录路径
    """
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def get_timestamp() -> float:
    """获取当前时间戳
    
    Returns:
        float: Unix 时间戳
    """
    return time.time()


def format_file_size(size_bytes: int) -> str:
    """格式化文件大小显示
    
    Args:
        size_bytes: 文件大小（字节）
        
    Returns:
        str: 格式化的文件大小字符串
    """
    if size_bytes == 0:
        return "0B"

    size_names = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1

    return f"{size_bytes:.1f}{size_names[i]}"


def truncate_text(text: str, max_length: int = 100, suffix: str = "...") -> str:
    """截断文本到指定长度
    
    Args:
        text: 原始文本
        max_length: 最大长度
        suffix: 截断后的后缀
        
    Returns:
        str: 截断后的文本
    """
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix


async def safe_await(coro, timeout: float = 30.0, default: Any = None) -> Any:
    """安全地等待协程执行，带超时保护
    
    Args:
        coro: 协程对象
        timeout: 超时时间（秒）
        default: 超时时的默认返回值
        
    Returns:
        Any: 协程的返回值或默认值
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        return default
    except (asyncio.CancelledError, RuntimeError):
        return default


class RateLimiter:
    """简单的速率限制器"""

    def __init__(self, max_calls: int, time_window: float):
        """初始化速率限制器
        
        Args:
            max_calls: 时间窗口内的最大调用次数
            time_window: 时间窗口长度（秒）
        """
        self.max_calls = max_calls
        self.time_window = time_window
        self.calls: List[float] = []

    async def acquire(self) -> bool:
        """获取执行权限
        
        Returns:
            bool: 是否获得执行权限
        """
        now = time.time()

        # 清理过期的调用记录
        self.calls = [call_time for call_time in self.calls if now - call_time < self.time_window]

        # 检查是否超过限制
        if len(self.calls) >= self.max_calls:
            return False

        # 记录本次调用
        self.calls.append(now)
        return True

    async def wait_if_needed(self) -> None:
        """如果需要则等待直到可以执行"""
        while not await self.acquire():
            await asyncio.sleep(0.1)


class AsyncTaskManager:
    """异步任务管理器"""

    def __init__(self):
        """初始化任务管理器"""
        self.tasks: List[asyncio.Task] = []

    def add_task(self, coro) -> asyncio.Task:
        """添加任务
        
        Args:
            coro: 协程对象
            
        Returns:
            asyncio.Task: 创建的任务
        """
        task = asyncio.create_task(coro)
        self.tasks.append(task)

        # 添加完成回调以清理任务列表
        task.add_done_callback(self._task_done_callback)

        return task

    def _task_done_callback(self, task: asyncio.Task):
        """任务完成回调"""
        if task in self.tasks:
            self.tasks.remove(task)

    async def cancel_all(self):
        """取消所有任务"""
        for task in self.tasks[:]:  # 创建副本以避免修改时迭代
            if not task.done():
                task.cancel()

        # 等待所有任务完成或取消
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

        self.tasks.clear()

    def get_active_count(self) -> int:
        """获取活跃任务数量
        
        Returns:
            int: 活跃任务数量
        """
        return len([task for task in self.tasks if not task.done()])


# 创建全局任务管理器实例
async_task_manager = AsyncTaskManager()
