"""模块名称：Discord 客户端管理器
主要功能：管理 Discord Bot 客户端连接和事件处理
"""

import asyncio
import traceback
import discord
from ..logger import logger
from ..config import global_config, is_user_allowed


class DiscordClientManager:
    """Discord 客户端管理器
    
    负责管理 Discord Bot 的连接、事件处理和消息队列
    
    Attributes:
        client (discord.Client | None): Discord 客户端实例
        message_queue (asyncio.Queue): 消息队列
        is_connected (bool): 连接状态
        is_shutting_down (bool): 是否正在关闭
        is_reconnecting (bool): 是否正在重连
    """

    def __init__(self):
        """初始化 Discord 客户端管理器"""
        self.client = None
        self.message_queue = asyncio.Queue()
        self.is_connected = False
        self.is_shutting_down = False
        self.is_reconnecting = False
        self._reconnect_task = None
        self._setup_client()

    def _setup_client(self):
        """设置 Discord 客户端
        
        配置 Discord 客户端的权限意图并注册事件处理器
        """
        intents = discord.Intents.default()
        discord_intents = global_config.discord.intents

        intents.messages = discord_intents.get("messages", True)
        intents.guilds = discord_intents.get("guilds", True)
        intents.dm_messages = discord_intents.get("dm_messages", True)
        intents.message_content = discord_intents.get("message_content", True)

        logger.debug(
            f"Discord 权限意图: messages={intents.messages}, guilds={intents.guilds}, "
            f"dm_messages={intents.dm_messages}, message_content={intents.message_content}"
        )

        # 创建 Discord 客户端
        self.client = discord.Client(intents=intents)

        # 使用装饰器方式注册事件处理器
        @self.client.event
        async def on_ready():
            await self._on_ready()

        @self.client.event
        async def on_message(message):
            await self._on_message(message)

        @self.client.event
        async def on_error(event, *args, **kwargs):
            await self._on_error(event, *args, **kwargs)

        @self.client.event
        async def on_disconnect():
            await self._on_disconnect()

        @self.client.event
        async def on_resume():
            await self._on_resume()

        logger.debug("Discord 客户端初始化完成")

    async def _on_ready(self):
        """Discord 客户端就绪事件处理器
        
        当 Discord 客户端连接成功并准备就绪时调用
        """
        self.is_connected = True
        logger.info(f"Discord 客户端已连接: {self.client.user}")
        logger.info(f"Bot 已加入 {len(self.client.guilds)} 个服务器")

        # 显示加入的服务器信息
        for guild in self.client.guilds:
            logger.debug(f"服务器: {guild.name} (ID: {guild.id})")
            # 显示前几个频道
            text_channels = guild.text_channels[:3]  # 只显示前3个频道
            for channel in text_channels:
                logger.debug(f"  - 频道: {channel.name} (ID: {channel.id})")

        logger.info("Discord 客户端准备就绪，等待消息事件...")

    async def _on_error(self, event: str, *args, **kwargs):
        """Discord 客户端错误事件处理器
        
        Args:
            event: 发生错误的事件名称
            *args: 事件参数
            **kwargs: 事件关键字参数
        """
        logger.error(f"Discord 事件 {event} 发生错误: {args}, {kwargs}")

    async def _on_disconnect(self):
        """Discord 客户端断开连接事件处理器"""
        self.is_connected = False
        logger.warning("Discord 客户端连接断开")

    async def _on_resume(self):
        """Discord 客户端重新连接事件处理器"""
        self.is_connected = True
        logger.info("Discord 客户端连接已恢复")

    async def _on_message(self, message: discord.Message):
        """Discord 消息事件处理器
        
        处理接收到的 Discord 消息，进行基本过滤后放入消息队列
        
        Args:
            message: Discord 消息对象
        """
        try:
            # 详细的消息来源信息
            channel_info = (f"频道: {message.channel.name}"
                           if hasattr(message.channel, 'name') else "私信频道")
            guild_info = f"服务器: {message.guild.name}" if message.guild else "私信"

            logger.debug("收到消息事件:")
            logger.debug(f"  消息ID: {message.id}")
            logger.debug(f"  作者: {message.author.display_name} (ID: {message.author.id})")
            logger.debug(f"  内容: '{message.content}'")
            logger.debug(f"  {channel_info} (ID: {message.channel.id})")
            logger.debug(f"  {guild_info} (ID: {message.guild.id if message.guild else 'N/A'})")
            logger.debug(f"  消息类型: {type(message.channel).__name__}")

            # 忽略机器人自己发送的消息
            if message.author == self.client.user:
                logger.debug("忽略机器人自己发送的消息")
                return

            # 检查黑白名单
            guild_id = message.guild.id if message.guild else None
            channel_id = message.channel.id

            # 检查是否为子区消息
            is_thread_message = hasattr(message.channel, 'parent') and message.channel.parent is not None
            thread_id = None

            if is_thread_message:
                thread_id = message.channel.id  # 子区ID
                # 对于子区消息，如果继承父频道权限，则使用父频道ID进行权限检查
                if global_config.chat.inherit_channel_permissions:
                    channel_id = message.channel.parent.id if message.channel.parent else channel_id
                    logger.debug(f"子区消息继承父频道权限: 子区ID={thread_id}, 父频道ID={channel_id}")
                else:
                    logger.debug(f"子区消息使用独立权限: 子区ID={thread_id}")

            logger.debug(f"权限检查: 用户ID={message.author.id}, 服务器ID={guild_id}, 频道ID={channel_id}, 子区ID={thread_id}, 是否子区={is_thread_message}")

            if not is_user_allowed(global_config, message.author.id, guild_id, channel_id, thread_id, is_thread_message):
                if is_thread_message:
                    logger.warning(f"用户 {message.author.id} 或子区 {thread_id} 不在允许列表中，忽略消息")
                else:
                    logger.warning(f"用户 {message.author.id} 或频道 {channel_id} 不在允许列表中，忽略消息")
                return

            # 将消息放入队列等待处理
            await self.message_queue.put(message)
            logger.debug(f"成功将 Discord 消息放入队列: {message.id}, 队列大小: {self.message_queue.qsize()}")

        except (AttributeError, TypeError, RuntimeError) as e:
            logger.error(f"处理 Discord 消息时发生错误: {e}")
            logger.error(f"错误详情: {traceback.format_exc()}")

    async def _reset_client(self):
        """
        重置客户端连接
        """
        # 关闭现有连接
        if self.client:
            try:
                if not self.client.is_closed():
                    await self.client.close()
                    logger.debug("旧客户端已关闭")
            except Exception as e:
                logger.warning(f"关闭旧客户端时出错: {e}")


        # 重新创建客户端
        self._setup_client()

        # 标记为未连接
        self.is_connected = False

    async def start(self):
        """启动 Discord 客户端
        
        Raises:
            Exception: 当启动失败时抛出异常
        """
        # 获取重试配置
        retry_config = global_config.discord.retry
        retry_delay = retry_config.get('retry_delay', 5)

        logger.info(f"正在启动 Discord 客户端... (重试间隔: {retry_delay}s)")

        last_error = None
        attempt = 0
        while True:
            try:
                if attempt > 0:
                    logger.info(f"第 {attempt} 次重试启动 Discord 客户端...")
                    # 等待重试间隔
                    await asyncio.sleep(retry_delay)

                    # 重置客户端（避免连接状态问题）
                    await self._reset_client()

                # 直接启动客户端，让background_tasks处理连接监控
                logger.debug("开始尝试连接到Discord...")
                await self.client.start(global_config.discord.token)

                # 如果执行到这里，说明连接断开了
                logger.warning("Discord 客户端连接意外断开")
                last_error = None
                break  # 正常断开不需要重试

            except (discord.LoginFailure, discord.HTTPException) as e:
                last_error = str(e)
                logger.warning(f"第 {attempt + 1} 次尝试失败: {last_error}")

                # 检查是否是Token错误
                if "login" in str(e).lower() or "token" in str(e).lower() or "unauthorized" in str(e).lower():
                    logger.error("Token 相关错误，请检查 Discord Bot Token 是否正确")
                    raise Exception(f"Discord 客户端启动失败: {last_error}") from e

            except (ConnectionError, TimeoutError, OSError) as e:
                last_error = str(e)
                logger.warning(f"第 {attempt + 1} 次尝试失败: {last_error}")

                # 记录详细错误信息
                if "信号灯超时" in str(e) or "timeout" in str(e).lower():
                    logger.warning("检测到网络超时，可能是网络连接问题或DNS拦截")
                elif "ssl" in str(e).lower():
                    logger.warning("检测到SSL错误，可能是证书问题或网络拦截")
                elif "name resolution" in str(e).lower() or "dns" in str(e).lower():
                    logger.warning("检测到DNS解析问题，可能是网络拦截或DNS污染")

                attempt += 1
                continue

            except Exception as e:
                last_error = str(e)
                logger.error(f"第 {attempt + 1} 次尝试时发生未知错误: {last_error}")
                attempt += 1
                continue

            attempt += 1

        logger.info("Discord 客户端已停止运行")
        return

    async def stop(self):
        """停止 Discord 客户端"""
        self.is_shutting_down = True

        # 关闭Discord客户端
        if self.client and not self.client.is_closed():
            await self.client.close()
            logger.info("Discord 客户端已关闭")

    async def force_reconnect(self):
        """强制重连Discord客户端
        
        由background_tasks调用，用于处理连接断开的情况
        """
        if self.is_shutting_down:
            logger.debug("系统正在关闭，跳过重连")
            return

        # 防止重复重连
        if self.is_reconnecting:
            logger.debug("已有重连任务正在进行，跳过此次重连请求")
            return

        logger.info("强制重连Discord客户端...")
        self.is_reconnecting = True

        try:
            # 标记为未连接
            self.is_connected = False

            # 取消之前的重连任务（如果存在）
            if self._reconnect_task and not self._reconnect_task.done():
                logger.debug("取消之前的重连任务")
                self._reconnect_task.cancel()
                try:
                    await self._reconnect_task
                except asyncio.CancelledError:
                    pass

            # 关闭现有连接（设置较短超时，避免卡住）
            if self.client and not self.client.is_closed():
                try:
                    await asyncio.wait_for(self.client.close(), timeout=3.0)
                    logger.info("Discord客户端连接已断开")
                except asyncio.TimeoutError:
                    logger.warning("关闭Discord客户端超时，强制继续")

            # 短暂等待确保连接完全关闭
            await asyncio.sleep(0.5)

            # 重新创建客户端
            await self._reset_client()
            logger.info("Discord客户端已重置，启动重连任务...")

            # 启动新的连接（异步进行，不阻塞监控任务）
            self._reconnect_task = asyncio.create_task(self._reconnect_client())
            logger.debug(f"重连任务已创建: {self._reconnect_task}")

        except Exception as e:
            logger.error(f"强制重连时发生错误: {e}")
            self.is_connected = False
            self.is_reconnecting = False
            logger.debug("force_reconnect异常，已重置is_reconnecting=False")

    async def _reconnect_client(self):
        """异步重连客户端"""
        try:
            # 获取重试配置
            retry_config = global_config.discord.retry
            retry_delay = retry_config.get('retry_delay', 5)

            attempt = 0
            while not self.is_shutting_down:
                try:
                    if attempt > 0:
                        logger.info(f"第 {attempt} 次重连尝试...")
                        await asyncio.sleep(retry_delay)
                        await self._reset_client()

                    logger.debug("开始连接到Discord...")
                    await self.client.start(global_config.discord.token)

                    # 如果执行到这里，说明连接成功后又断开了
                    logger.info("Discord连接已断开，准备重试")
                    self.is_connected = False
                    attempt += 1

                except (discord.LoginFailure, discord.HTTPException) as e:
                    logger.error(f"重连过程中出现认证错误，停止重连: {e}")
                    self.is_reconnecting = False
                    return

                except asyncio.CancelledError:
                    logger.info("重连任务被取消")
                    self.is_reconnecting = False
                    raise

                except Exception as e:
                    logger.warning(f"第 {attempt + 1} 次重连失败: {e}")
                    attempt += 1
                    continue

            if not self.is_shutting_down:
                logger.warning("重连循环结束，未能成功重连")

        except asyncio.CancelledError:
            logger.info("重连任务被取消")
            raise
        except Exception as e:
            logger.error(f"重连过程中发生错误: {e}")
        finally:
            # 确保重置重连标志
            self.is_reconnecting = False
            logger.debug("重连任务结束，is_reconnecting=False")

    async def get_channel(self, channel_id: int) -> discord.abc.Messageable | None:
        """获取频道对象
        
        Args:
            channel_id: 频道 ID
            
        Returns:
            discord.abc.Messageable | None: 频道对象，获取失败时返回 None
        """
        if not self.client:
            return None
        return self.client.get_channel(channel_id)

    async def get_user(self, user_id: int) -> discord.User | None:
        """获取用户对象
        
        Args:
            user_id: 用户 ID
            
        Returns:
            discord.User | None: 用户对象，获取失败时返回 None
        """
        if not self.client:
            return None
        return self.client.get_user(user_id)


# 创建全局客户端实例
discord_client: DiscordClientManager = DiscordClientManager()
