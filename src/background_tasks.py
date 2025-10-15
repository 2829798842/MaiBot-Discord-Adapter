"""模块名称：后台任务管理器
主要功能：管理Discord连接监控和其他后台任务
"""

import asyncio
from typing import Optional
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

            # 如果正在重连，跳过本次检查
            if hasattr(self.client_manager, 'is_reconnecting') and self.client_manager.is_reconnecting:
                logger.debug("正在重连中，跳过本次连接状态检查")
                return

            # 检查连接状态，设置超时保护
            try:
                await asyncio.wait_for(self._check_connection_status(), timeout=30)
            except asyncio.TimeoutError:
                logger.error("连接状态检测超时，可能网络存在问题")
                if self.client_manager and not getattr(self.client_manager, 'is_reconnecting', False):
                    logger.warning("触发超时重连")
                    self.client_manager.is_connected = False
                    if hasattr(self.client_manager, 'force_reconnect'):
                        await self.client_manager.force_reconnect()
            except (ConnectionError, RuntimeError) as e:
                logger.error(f"连接监控任务出错: {e}")
                if self.client_manager and not getattr(self.client_manager, 'is_reconnecting', False):
                    self.client_manager.is_connected = False
                    if hasattr(self.client_manager, 'force_reconnect'):
                        await self.client_manager.force_reconnect()
            except Exception as e:
                logger.error(f"连接监控任务发生未知异常: {e}")
                if self.client_manager and not getattr(self.client_manager, 'is_reconnecting', False):
                    self.client_manager.is_connected = False
                    if hasattr(self.client_manager, 'force_reconnect'):
                        await self.client_manager.force_reconnect()

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
        """检查连接状态（快速检查，避免卡住）"""
        if not self.client_manager.client:
            logger.debug("Discord客户端未初始化")
            return

        client = self.client_manager.client

        # 快速检查：客户端是否已关闭
        if client.is_closed():
            if self.client_manager.is_connected:
                logger.warning("检测到Discord连接已关闭")
                self.client_manager.is_connected = False
                if hasattr(self.client_manager, 'force_reconnect'):
                    await self.client_manager.force_reconnect()
            return

        # 使用超时保护的连接状态检查
        try:
            # 设置3秒超时，避免卡住
            check_task = asyncio.create_task(self._quick_check_ready(client))
            is_ready, latency = await asyncio.wait_for(check_task, timeout=3.0)

            # 检查latency是否异常
            latency_invalid = (
                latency is None or
                latency == float('inf') or
                latency != latency or  # NaN check
                latency < 0
            )

            if is_ready and not latency_invalid and 0 < latency < 10.0:
                # 连接正常
                if not self.client_manager.is_connected:
                    logger.info("检测到Discord连接已恢复")
                    self.client_manager.is_connected = True
                logger.debug(f"Discord连接状态正常 (延迟: {latency:.3f}s)")
            elif is_ready and (latency_invalid or latency >= 10.0):
                # 连接存在但延迟异常
                logger.warning(f"Discord连接延迟异常: {latency}")
                if self.client_manager.is_connected:
                    logger.warning("由于延迟异常，触发重连")
                    self.client_manager.is_connected = False
                    if hasattr(self.client_manager, 'force_reconnect'):
                        await self.client_manager.force_reconnect()
            else:
                # 未就绪
                if self.client_manager.is_connected:
                    logger.warning("Discord客户端未就绪，触发重连")
                    self.client_manager.is_connected = False
                    if hasattr(self.client_manager, 'force_reconnect'):
                        await self.client_manager.force_reconnect()
                else:
                    logger.debug("Discord客户端正在连接中...")

        except asyncio.TimeoutError:
            # 检查超时，说明获取状态时卡住了
            logger.error("连接状态检查超时（3秒），客户端可能已失去响应")
            if self.client_manager.is_connected:
                logger.warning("触发超时重连")
                self.client_manager.is_connected = False
                if hasattr(self.client_manager, 'force_reconnect'):
                    await self.client_manager.force_reconnect()
        except Exception as e:
            logger.error(f"检查连接状态时发生异常: {e}")
            if self.client_manager.is_connected:
                self.client_manager.is_connected = False
                if hasattr(self.client_manager, 'force_reconnect'):
                    await self.client_manager.force_reconnect()

    async def _quick_check_ready(self, client):
        """快速检查客户端就绪状态和延迟"""
        try:
            is_ready = client.is_ready()
            latency = client.latency
            return is_ready, latency
        except Exception as e:
            logger.debug(f"快速检查时出错: {e}")
            return False, None

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
        logger.debug("Reaction事件处理任务初始化完成")

    def start(self):
        """启动reaction事件监听"""
        if not self.client_manager or not self.client_manager.client:
            logger.error("Discord客户端未初始化，无法启动reaction事件监听")
            return

        if self.is_running:
            logger.warning("Reaction事件监听已在运行")
            return

        client = self.client_manager.client

        # 仅注册一次事件处理器，防止重复覆盖
        if not getattr(client, '_reaction_events_registered', False):
            @client.event
            async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
                """监听表情添加事件"""
                await self._on_raw_reaction_add(payload)

            @client.event
            async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
                """监听表情移除事件"""
                await self._on_raw_reaction_remove(payload)

            client._reaction_events_registered = True
            logger.debug("Reaction事件处理器已注册到Discord客户端")
        else:
            logger.debug("Reaction事件处理器已存在，直接启用监听")

        self.is_running = True
        logger.info("Reaction事件监听已启动")

    async def _on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """处理表情添加事件
        
        Args:
            payload: Reaction事件数据
        """
        try:
            if not self.is_running:
                logger.debug("Reaction事件监听已停止，忽略reaction_add事件")
                return

            client = self.client_manager.client
            if not client or not client.user:
                logger.warning("Discord客户端未就绪，忽略reaction_add事件")
                return

            # 忽略机器人自己的reaction
            if payload.user_id == client.user.id:
                logger.debug("忽略机器人自己的reaction")
                return

            # 获取基本信息
            guild_id = payload.guild_id
            channel_id = payload.channel_id
            user_id = payload.user_id

            # 获取channel对象判断是否为子区
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

            # 如果是子区且继承父频道权限
            if is_thread and global_config.chat.inherit_channel_permissions:
                parent_channel_id = channel.parent.id if getattr(channel, 'parent', None) else channel_id
                logger.debug(f"子区reaction继承父频道权限: 子区ID={thread_id}, 父频道ID={parent_channel_id}")
                check_channel_id = parent_channel_id
            else:
                check_channel_id = channel_id

            logger.debug(
                "Reaction权限检查: 用户ID=%s, 服务器ID=%s, 频道ID=%s, 子区ID=%s",
                user_id,
                guild_id,
                check_channel_id,
                thread_id,
            )

            if not is_user_allowed(global_config, user_id, guild_id, check_channel_id, thread_id, is_thread):
                logger.warning(f"用户 {user_id} 或频道 {check_channel_id} 不在允许列表中，忽略reaction")
                return

            # 转发到消息处理器
            logger.debug(f"收到reaction_add事件: 用户={user_id}, 消息={payload.message_id}, emoji={payload.emoji}")
            await self.message_handler.handle_reaction_event('reaction_add', payload)

        except Exception as e:
            logger.error(f"处理reaction_add事件时发生错误: {e}")
            import traceback
            logger.error(f"错误详情: {traceback.format_exc()}")

    async def _on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        """处理表情移除事件
        
        Args:
            payload: Reaction事件数据
        """
        try:
            if not self.is_running:
                logger.debug("Reaction事件监听已停止，忽略reaction_remove事件")
                return

            client = self.client_manager.client
            if not client or not client.user:
                logger.warning("Discord客户端未就绪，忽略reaction_remove事件")
                return

            # 忽略机器人自己的reaction
            if payload.user_id == client.user.id:
                logger.debug("忽略机器人自己的reaction")
                return

            # 获取基本信息
            guild_id = payload.guild_id
            channel_id = payload.channel_id
            user_id = payload.user_id

            # 检查权限
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

            # 如果是子区且继承父频道权限
            if is_thread and global_config.chat.inherit_channel_permissions:
                parent_channel_id = channel.parent.id if getattr(channel, 'parent', None) else channel_id
                check_channel_id = parent_channel_id
            else:
                check_channel_id = channel_id

            if not is_user_allowed(global_config, user_id, guild_id, check_channel_id, thread_id, is_thread):
                logger.warning(f"用户 {user_id} 或频道 {check_channel_id} 不在允许列表中，忽略reaction")
                return

            # 转发到消息处理器
            logger.debug(f"收到reaction_remove事件: 用户={user_id}, 消息={payload.message_id}, emoji={payload.emoji}")
            await self.message_handler.handle_reaction_event('reaction_remove', payload)

        except Exception as e:
            logger.error(f"处理reaction_remove事件时发生错误: {e}")
            import traceback
            logger.error(f"错误详情: {traceback.format_exc()}" )

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
    
    统一管理所有后台任务的生命周期
    
    Attributes:
        connection_monitor: 连接监控任务
        reaction_event_task: reaction事件处理任务
        tasks: 所有注册的任务列表
    """

    def __init__(self):
        """初始化后台任务管理器"""
        self.connection_monitor: Optional[ConnectionMonitorTask] = None
        self.reaction_event_task: Optional['ReactionEventTask'] = None
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


# 全局后台任务管理器实例
background_task_manager = BackgroundTaskManager()
