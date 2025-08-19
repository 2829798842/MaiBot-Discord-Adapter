"""模块名称：后台任务管理器
主要功能：管理Discord连接监控和其他后台任务
"""

import asyncio
from typing import Optional
from discord.ext import tasks

from .logger import logger
from .config import global_config


class ConnectionMonitorTask:
    """Discord连接监控任务类
    
    负责定期检查Discord连接状态并处理重连逻辑
    
    Attributes:
        client_manager: Discord客户端管理器实例
        monitor_task: 连接监控任务实例
        is_running: 任务运行状态
    """

    def __init__(self, client_manager):
        """初始化连接监控任务
        
        Args:
            client_manager: Discord客户端管理器实例
        """
        self.client_manager = client_manager
        self.monitor_task: Optional[tasks.Loop] = None
        self.is_running: bool = False

        # 初始化监控任务
        self._init_monitor_task()

    def _init_monitor_task(self):
        """初始化监控任务"""
        # 获取监控间隔配置，默认30秒
        retry_config = global_config.discord.retry
        check_interval = retry_config.get('connection_check_interval', 30)

        @tasks.loop(seconds=check_interval)
        async def connection_monitor():
            """连接监控主循环"""
            if not self.client_manager or self.client_manager.is_shutting_down:
                return

            try:
                await self._check_connection_status()
            except (ConnectionError, RuntimeError) as e:
                logger.error(f"连接监控任务出错: {e}")
                # 发生错误时也标记为需要重连
                if self.client_manager:
                    self.client_manager.is_connected = False

        @connection_monitor.before_loop
        async def before_monitor():
            """监控任务启动前的准备工作"""
            logger.debug("等待Discord客户端连接成功...")
            # 等待客户端初始化并连接成功
            while (not self.client_manager or
                   not hasattr(self.client_manager, 'client') or
                   not self.client_manager.is_connected):
                await asyncio.sleep(2)
                if (self.client_manager and
                    hasattr(self.client_manager, 'is_shutting_down') and
                    self.client_manager.is_shutting_down):
                    logger.debug("检测到关闭信号，监控任务不启动")
                    return
            logger.info("Discord客户端已连接，开始监控连接状态")

        @connection_monitor.error
        async def monitor_error(error):
            """监控任务错误处理"""
            logger.error(f"连接监控任务发生错误: {error}")
            # 标记连接断开，触发重连逻辑
            if self.client_manager:
                self.client_manager.is_connected = False

        @connection_monitor.after_loop
        async def after_monitor():
            """监控任务结束后的清理工作"""
            logger.debug("连接监控任务已停止")

        self.monitor_task = connection_monitor

    async def _check_connection_status(self):
        """检查连接状态"""
        if not self.client_manager.client:
            logger.debug("Discord客户端未初始化")
            return

        # 检查客户端连接状态
        client = self.client_manager.client
        if client.is_closed():
            if self.client_manager.is_connected:
                logger.warning("检测到Discord连接已关闭")
                self.client_manager.is_connected = False
        else:
            # 使用Discord客户端的实际连接状态检查
            # is_ready() 检查客户端是否完全连接并准备就绪
            # latency 检查网络延迟，如果无法获取则可能断开连接
            try:
                is_ready = client.is_ready()
                latency = client.latency

                # 检查是否真正连接并且网络状态正常
                if is_ready and latency > 0 and latency < 10.0:  # 延迟在合理范围内
                    # 连接正常，更新状态
                    if not self.client_manager.is_connected:
                        logger.info("检测到Discord连接已恢复")
                        self.client_manager.is_connected = True
                    logger.debug(f"Discord连接状态正常 (延迟: {latency:.3f}s)")
                elif is_ready and latency >= 10.0:
                    # 连接延迟过高，可能网络有问题
                    logger.warning(f"Discord连接延迟过高: {latency:.3f}s")
                    if self.client_manager.is_connected:
                        logger.warning("由于延迟过高，标记连接为不稳定")
                else:
                    # 客户端存在但还未完全连接或网络有问题
                    if self.client_manager.is_connected:
                        logger.debug("Discord客户端可能正在重连中...")
                        self.client_manager.is_connected = False
                    else:
                        logger.debug("Discord客户端存在但未就绪，可能正在连接中...")

            except (AttributeError, ConnectionError) as e:
                logger.debug(f"检查连接状态时出错: {e}")
                if self.client_manager.is_connected:
                    logger.warning("Discord连接状态检查失败，可能连接不稳定")
                    self.client_manager.is_connected = False

    def start(self):
        """启动监控任务"""
        if not self.monitor_task:
            logger.error("监控任务未初始化")
            return

        if self.monitor_task.is_running():
            logger.warning("监控任务已在运行")
            return

        try:
            self.monitor_task.start()
            self.is_running = True
            logger.info("连接监控任务已启动")
        except (RuntimeError, AttributeError) as e:
            logger.error(f"启动监控任务失败: {e}")

    def stop(self):
        """停止监控任务"""
        if not self.monitor_task:
            return

        if self.monitor_task.is_running():
            self.monitor_task.cancel()
            logger.info("连接监控任务已停止")

        self.is_running = False

    def restart(self):
        """重启监控任务"""
        logger.info("重启连接监控任务")
        self.stop()
        # 等待一小段时间确保任务完全停止
        asyncio.create_task(self._delayed_start())

    async def _delayed_start(self):
        """延迟启动任务"""
        await asyncio.sleep(1)
        self.start()


class BackgroundTaskManager:
    """后台任务管理器
    
    统一管理所有后台任务的生命周期
    
    Attributes:
        connection_monitor: 连接监控任务
        tasks: 所有注册的任务列表
    """

    def __init__(self):
        """初始化后台任务管理器"""
        self.connection_monitor: Optional[ConnectionMonitorTask] = None
        self.tasks: list = []
        logger.debug("后台任务管理器初始化完成")

    def register_connection_monitor(self, client_manager):
        """注册连接监控任务
        
        Args:
            client_manager: Discord客户端管理器实例
        """
        self.connection_monitor = ConnectionMonitorTask(client_manager)
        self.tasks.append(self.connection_monitor)
        logger.debug("连接监控任务已注册")

    def start_all_tasks(self):
        """启动所有任务"""
        logger.info("启动所有后台任务")
        for task in self.tasks:
            if hasattr(task, 'start'):
                task.start()

    def stop_all_tasks(self):
        """停止所有任务"""
        logger.info("停止所有后台任务")
        for task in self.tasks:
            if hasattr(task, 'stop'):
                task.stop()

    def restart_all_tasks(self):
        """重启所有任务"""
        logger.info("重启所有后台任务")
        for task in self.tasks:
            if hasattr(task, 'restart'):
                task.restart()


# 全局后台任务管理器实例
background_task_manager = BackgroundTaskManager()
