"""模块名称：后台任务管理器
主要功能：管理Discord连接监控和其他后台任务
"""

import asyncio
import traceback
from typing import Optional
from time import time as get_time
import discord
from discord.ext import tasks

from .logger import logger
from .config import global_config, is_user_allowed


class ConnectionMonitorTask:
    """Discord连接监控任务类
    
    负责定期检查Discord连接状态并处理重连逻辑
    
    Attributes:
        client_manager: Discord客户端管理器实例
        monitor_task: 连接监控任务实例
        is_running: 任务运行状态
    """

    def __init__(self, client_manager, task_manager=None):
        """初始化连接监控任务
        
        Args:
            client_manager: Discord客户端管理器实例
            task_manager: 后台任务管理器实例（可选）
        """
        self.client_manager = client_manager
        self.task_manager = task_manager
        self.monitor_task: Optional[tasks.Loop] = None
        self.is_running: bool = False
        self._last_health_check = 0  # 上次主动健康检查时间
        self._health_check_interval = 60  # 每60秒进行一次主动健康检查
        self._health_check_failures = 0  # 连续健康检查失败次数

        # 初始化监控任务
        self._init_monitor_task()

    def _init_monitor_task(self):
        """初始化监控任务"""
        # 获取监控间隔配置，默认30秒
        retry_config = global_config.discord.retry
        check_interval = retry_config.get('connection_check_interval', 30)

        @tasks.loop(seconds=check_interval, reconnect=True)
        async def connection_monitor():
            """连接监控主循环"""
            if not self.client_manager or self.client_manager.is_shutting_down:
                return

            await self._check_connection_status()

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

        client = self.client_manager.client

        # 快速检查：客户端是否已关闭
        if client.is_closed():
            if self.client_manager.is_connected:
                logger.warning("检测到Discord连接已关闭")
                self.client_manager.is_connected = False
            return

        try:
            is_ready, latency = await asyncio.wait_for(
                self._quick_check_ready(client),
                timeout=3.0
            )

            # 检查latency是否有效
            latency_valid = (
                latency is not None and
                latency != float('inf') and
                latency == latency and  # 不是NaN
                latency >= 0
            )

            if is_ready and latency_valid and latency < 10.0:
                # 连接正常
                if not self.client_manager.is_connected:
                    logger.info("Discord连接已恢复")
                    self.client_manager.is_connected = True

                    # 检查是否需要重新注册事件处理器
                    if self.task_manager:
                        self.task_manager.check_and_reregister_events(client)

                # 定期主动健康检查（防止僵尸连接）
                current_time = get_time()
                if current_time - self._last_health_check >= self._health_check_interval:
                    logger.debug("执行主动健康检查...")
                    health_ok = await self._active_health_check(client)
                    self._last_health_check = current_time

                    if not health_ok:
                        self._health_check_failures += 1
                        logger.warning(
                            f"主动健康检查失败 ({self._health_check_failures}/3)"
                        )


                        if self._health_check_failures >= 3:
                            logger.error("检测到连接坏死，触发重连")
                            self._health_check_failures = 0
                            self.client_manager.is_connected = False
                            if hasattr(self.client_manager, 'force_reconnect'):
                                await self.client_manager.force_reconnect()
                    else:
                        self._health_check_failures = 0

                logger.debug(f"Discord连接正常 (延迟: {latency:.3f}s)")

            elif is_ready and (not latency_valid or latency >= 10.0):
                # 延迟异常，记录但不干预（Discord会自己处理）
                logger.warning(f"Discord延迟异常: {latency}，等待自动恢复")
                self.client_manager.is_connected = False

            else:
                # 未就绪
                if self.client_manager.is_connected:
                    logger.warning("Discord客户端未就绪")
                    self.client_manager.is_connected = False
                else:
                    logger.debug("Discord客户端正在连接中...")

        except asyncio.TimeoutError:
            logger.warning("连接状态检查超时，可能网络存在问题")
            self.client_manager.is_connected = False
        except Exception as e:
            logger.error(f"检查连接状态时发生异常: {e}")
            self.client_manager.is_connected = False

    async def _quick_check_ready(self, client):
        """快速检查客户端就绪状态和延迟"""
        try:
            is_ready = client.is_ready()
            latency = client.latency
            return is_ready, latency
        except Exception as e:
            logger.debug(f"快速检查时出错: {e}")
            return False, None

    async def _active_health_check(self, client):
        """主动健康检查：发送实际请求验证连接活跃性
        
        Returns:
            bool: 检查是否通过
        """
        try:
            # 尝试获取自己的用户信息（轻量级请求）
            if client.user:
                user = await asyncio.wait_for(
                    client.fetch_user(client.user.id),
                    timeout=30.0
                )
                if user:
                    logger.debug("主动健康检查通过：成功获取用户信息")
                    return True

            logger.warning("主动健康检查失败：无法获取用户信息")
            return False

        except asyncio.TimeoutError:
            logger.warning("主动健康检查超时")
            return False
        except (discord.HTTPException, discord.NotFound) as e:
            logger.warning(f"主动健康检查失败：{e}")
            return False
        except Exception as e:
            logger.warning(f"主动健康检查异常：{e}")
            return False

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


class ReactionEventTask:
    """Reaction事件处理任务类
    
    负责监听Discord的reaction事件并转发到消息处理器
    
    Attributes:
        client_manager: Discord客户端管理器实例
        message_handler: 消息处理器实例
        is_running: 任务运行状态
    """

    def __init__(self, client_manager, message_handler):
        """初始化Reaction事件处理任务
        
        Args:
            client_manager: Discord客户端管理器实例
            message_handler: 消息处理器实例
        """
        self.client_manager = client_manager
        self.message_handler = message_handler
        self.is_running: bool = False
        self._events_registered: bool = False
        self._registered_client_id = None
        logger.debug("Reaction事件处理任务初始化完成")

    def start(self, force_register=False):
        """启动reaction事件监听
        
        Args:
            force_register: 客户端重置后强制重新注册
        """
        # 启动异步注册任务
        asyncio.create_task(self._async_start(force_register))

    async def _async_start(self, force_register=False):
        """异步启动并等待Discord ready"""
        # 等待Discord客户端就绪
        logger.info("等待Discord客户端就绪以注册reaction事件...")
        while True:
            if not self.client_manager or not self.client_manager.client:
                await asyncio.sleep(1)
                continue

            client = self.client_manager.client
            if client.is_ready():
                break

            await asyncio.sleep(1)

        logger.info("Discord客户端已就绪，开始注册reaction事件处理器")

        current_client_id = id(client)

        # 检查是否需要重新注册
        if self._events_registered and not force_register and self._registered_client_id == current_client_id:
            logger.warning("Reaction事件已在此client上注册，跳过")
            return

        # 强制注册或client对象已变化，需要重新注册
        if force_register or self._registered_client_id != current_client_id:
            logger.info("重新注册reaction事件处理器")
            self._events_registered = False

        if not self._events_registered:
            @client.event
            async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
                """监听表情添加事件"""
                await self._on_raw_reaction_add(payload)

            @client.event
            async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
                """监听表情移除事件"""
                await self._on_raw_reaction_remove(payload)

            self._events_registered = True
            self._registered_client_id = current_client_id
            logger.info("Reaction事件处理器已成功注册到Discord客户端")
        else:
            logger.debug("Reaction事件处理器已存在，直接启用监听")

        self.is_running = True
        logger.info("Reaction事件监听已启动")

    async def _on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """处理表情添加事件"""
        await self._process_reaction_event('reaction_add', payload)

    async def _on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        """处理表情移除事件"""
        await self._process_reaction_event('reaction_remove', payload)

    async def _process_reaction_event(self, event_type: str, payload: discord.RawReactionActionEvent):
        """处理reaction事件的通用逻辑（避免代码重复）"""
        try:
            if not self.is_running:
                logger.debug(f"Reaction事件监听已停止，忽略{event_type}事件")
                return

            client = self.client_manager.client
            if not client or not client.user:
                logger.warning(f"Discord客户端未就绪，忽略{event_type}事件")
                return

            # 过滤机器人自己的reaction
            if payload.user_id == client.user.id:
                logger.debug("忽略机器人自己的reaction")
                return

            # 获取基本信息
            guild_id = payload.guild_id
            channel_id = payload.channel_id
            user_id = payload.user_id

            # 获取channel对象以判断是否为子区
            channel = client.get_channel(channel_id)
            if not channel:
                try:
                    channel = await client.fetch_channel(channel_id)
                except (discord.NotFound, discord.Forbidden):
                    logger.warning(f"无法获取频道 {channel_id}")
                    return
                except discord.HTTPException as fetch_error:
                    logger.error(f"获取频道 {channel_id} 时发生错误: {fetch_error}")
                    return


            is_thread = isinstance(channel, discord.Thread)
            thread_id = channel_id if is_thread else None

            # 确定用于权限检查的频道ID（支持子区权限继承）
            if is_thread and global_config.chat.inherit_channel_permissions:
                parent = getattr(channel, 'parent', None)
                parent_channel_id = parent.id if parent else channel_id
                logger.debug(
                    f"子区reaction继承父频道权限: "
                    f"子区ID={thread_id}, 父频道ID={parent_channel_id}"
                )
                check_channel_id = parent_channel_id
            else:
                check_channel_id = channel_id

            logger.debug(
                f"Reaction权限检查: 用户ID={user_id}, 服务器ID={guild_id}, "
                f"频道ID={check_channel_id}, 子区ID={thread_id}"
            )

            # 权限验证
            if not is_user_allowed(
                global_config, user_id, guild_id, check_channel_id, thread_id, is_thread
            ):
                logger.warning(
                    f"用户 {user_id} 或频道 {check_channel_id} 不在允许列表中，"
                    f"忽略{event_type}事件"
                )
                return

            # 转发到消息处理器
            logger.debug(
                f"收到{event_type}事件: 用户={user_id}, "
                f"消息={payload.message_id}, emoji={payload.emoji}"
            )
            await self.message_handler.handle_reaction_event(event_type, payload)

        except Exception as e:
            logger.error(f"处理{event_type}事件时发生错误: {e}")
            logger.error(f"错误详情: {traceback.format_exc()}")

    def stop(self):
        """停止reaction事件监听"""
        self.is_running = False
        logger.info("Reaction事件监听已停止")

    def restart(self):
        """重启reaction事件监听"""
        logger.info("重启Reaction事件监听")
        self.stop()
        self.start()


class BackgroundTaskManager:
    """后台任务管理器

    
    Attributes:
        connection_monitor: 连接监控任务
        reaction_event_task: reaction事件处理任务
        tasks: 所有注册的任务列表
    """

    def __init__(self):
        """初始化后台任务管理器"""
        self.connection_monitor: Optional[ConnectionMonitorTask] = None
        self.reaction_event_task: Optional[ReactionEventTask] = None
        self.tasks: list = []
        self._last_client_id = None  # 用于检测客户端重置
        logger.debug("后台任务管理器初始化完成")

    def register_connection_monitor(self, client_manager):
        """注册连接监控任务
        
        Args:
            client_manager: Discord客户端管理器实例
        """
        self.connection_monitor = ConnectionMonitorTask(client_manager, task_manager=self)
        self.tasks.append(self.connection_monitor)
        logger.debug("连接监控任务已注册")

    def register_reaction_event_task(self, client_manager, message_handler):
        """注册reaction事件处理任务
        
        Args:
            client_manager: Discord客户端管理器实例
            message_handler: 消息处理器实例
        """
        self.reaction_event_task = ReactionEventTask(client_manager, message_handler)
        self.tasks.append(self.reaction_event_task)
        logger.debug("Reaction事件处理任务已注册")

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

    def check_and_reregister_events(self, client):
        """检查客户端是否重置，重新注册事件处理器"""
        current_client_id = id(client)

        if self._last_client_id is not None and self._last_client_id != current_client_id:
            logger.info("检测到Discord客户端已重置，重新注册所有事件处理器...")

            # 重新注册reaction事件
            if self.reaction_event_task:
                logger.info("重新注册reaction事件处理器...")
                self.reaction_event_task.start(force_register=True)

        self._last_client_id = current_client_id


# 全局后台任务管理器实例
background_task_manager = BackgroundTaskManager()
