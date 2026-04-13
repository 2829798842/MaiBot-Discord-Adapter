"""Discord 客户端管理器。

管理 Discord Bot 客户端连接、事件处理、连接监控和 Reaction 事件绑定。
"""

import asyncio
import traceback
from time import time as get_time
from typing import Any, Dict, Optional

import discord

from ..send_handler.thread_send_handler import ThreadRoutingManager
from .message_handler import DiscordMessageHandler


class DiscordClientManager:
    """Discord 客户端管理器。

    构造时接收所有外部依赖，不使用任何全局状态。
    内置连接健康监控和 Reaction 事件动态绑定。
    """

    def __init__(
        self,
        *,
        logger: Any,
        token: str,
        intents_config: Dict[str, bool],
        gateway_name: str,
        gateway_capability: Any,
        message_handler: DiscordMessageHandler,
        thread_routing_manager: ThreadRoutingManager,
        chat_filter: Any,
        filter_config: Any,
        connection_check_interval: int = 30,
        retry_delay: int = 5,
    ) -> None:
        """初始化管理器状态并创建 Discord 客户端与事件绑定。

        Args:
            logger: 日志记录器。
            token: Discord Bot 登录令牌。
            intents_config: 各 Gateway Intent 的开关配置（如 guild_messages、dm_messages、
                reactions 等）。
            gateway_name: 当前网关在路由层使用的名称。
            gateway_capability: 网关能力对象，用于将消息路由到 Host。
            message_handler: 入站消息处理器，负责 Discord 消息到 Host 结构的转换。
            thread_routing_manager: 子区/线程路由管理器，需与客户端实例绑定。
            chat_filter: 频道与用户维度的入站过滤逻辑。
            filter_config: 过滤相关配置（如是否忽略自身或机器人消息）。
            connection_check_interval: 连接健康监控的检查间隔（秒）。
            retry_delay: 启动或重连失败后的重试等待时间（秒）。
        """
        self._logger = logger
        self._token = token
        self._intents_config = intents_config
        self._gateway_name = gateway_name
        self._gateway_capability = gateway_capability
        self._message_handler = message_handler
        self._thread_routing_manager = thread_routing_manager
        self._chat_filter = chat_filter
        self._filter_config = filter_config
        self._connection_check_interval = connection_check_interval
        self._retry_delay = retry_delay

        self.client: Optional[discord.Client] = None
        self.is_connected: bool = False
        self.is_shutting_down: bool = False
        self.is_reconnecting: bool = False

        self._monitor_task: Optional[asyncio.Task[None]] = None
        self._reconnect_task: Optional[asyncio.Task[None]] = None

        self._registered_reaction_client_id: Optional[int] = None
        self._last_health_check: float = 0
        self._health_check_interval: float = 60
        self._health_check_failures: int = 0

        self._on_connected_callback: Optional[Any] = None
        self._on_disconnected_callback: Optional[Any] = None

        self.voice_manager: Optional[Any] = None

        self._setup_client()

    def set_lifecycle_callbacks(
        self,
        on_connected: Any = None,
        on_disconnected: Any = None,
    ) -> None:
        """注册连接就绪与断开时的生命周期回调。

        Args:
            on_connected: 连接就绪或恢复时调用的异步回调，可为 None。
            on_disconnected: 连接断开时调用的异步回调，可为 None。
        """
        self._on_connected_callback = on_connected
        self._on_disconnected_callback = on_disconnected

    def _setup_client(self) -> None:
        """根据配置创建 `discord.Client`、绑定路由并注册各类事件处理器。"""
        intents = discord.Intents.default()
        guild_messages = self._intents_config.get("guild_messages")
        if guild_messages is None:
            # 兼容旧映射键，避免历史配置或旧调用方直接传入 messages 时失效。
            guild_messages = self._intents_config.get("messages", True)

        intents.guild_messages = bool(guild_messages)
        intents.guilds = self._intents_config.get("guilds", True)
        intents.dm_messages = self._intents_config.get("dm_messages", True)
        intents.message_content = self._intents_config.get("message_content", True)
        intents.reactions = self._intents_config.get("reactions", True)
        intents.voice_states = self._intents_config.get("voice_states", False)

        client = discord.Client(intents=intents)
        self.client = client
        self._thread_routing_manager.bind_client(client)

        @client.event
        async def on_ready() -> None:
            await self._on_ready()

        @client.event
        async def on_message(message: discord.Message) -> None:
            await self._on_message(message)

        @client.event
        async def on_error(event: str, *args: Any, **kwargs: Any) -> None:
            self._logger.error(f"Discord 事件 {event} 发生错误")

        @client.event
        async def on_disconnect() -> None:
            self.is_connected = False
            self._logger.warning("Discord 客户端连接断开")
            if self._on_disconnected_callback:
                try:
                    await self._on_disconnected_callback()
                except Exception:
                    pass

        @client.event
        async def on_resume() -> None:
            self.is_connected = True
            self._logger.info("Discord 客户端连接已恢复")
            if self._on_connected_callback:
                try:
                    await self._on_connected_callback()
                except Exception:
                    pass

        @client.event
        async def on_voice_state_update(
            member: discord.Member,
            before: discord.VoiceState,
            after: discord.VoiceState,
        ) -> None:
            if self.voice_manager:
                try:
                    await self.voice_manager.on_voice_state_update(member, before, after)
                except Exception as exc:
                    self._logger.error(f"语音状态更新处理失败: {exc}")

    async def _on_ready(self) -> None:
        """Gateway 就绪时更新连接状态、记录日志、注册 Reaction 并触发连接回调。"""
        self.is_connected = True
        client = self.client
        if client is None:
            return
        self._logger.info(f"Discord 客户端已连接: {client.user}")
        self._logger.info(f"Bot 已加入 {len(client.guilds)} 个服务器")

        self._register_reaction_events()

        if self._on_connected_callback:
            try:
                await self._on_connected_callback()
            except Exception as exc:
                self._logger.warning(f"连接就绪回调执行失败: {exc}")

    async def _on_message(self, message: discord.Message) -> None:
        """处理入站频道/DM 消息：过滤后转换为 Host 结构并路由，必要时更新子区上下文。

        Args:
            message: Discord 推送的 `Message` 对象。
        """
        try:
            bot_user = getattr(self.client, "user", None)
            if bot_user and message.author.id == bot_user.id:
                if getattr(self._filter_config, "ignore_self_message", True):
                    return

            if message.author.bot:
                if getattr(self._filter_config, "ignore_bot_message", True):
                    return

            guild_id = str(message.guild.id) if message.guild else None
            channel_id = str(message.channel.id)
            channel_type = type(message.channel).__name__
            is_voice_chat_message = isinstance(
                message.channel, (discord.VoiceChannel, discord.StageChannel)
            )

            is_thread = hasattr(message.channel, "parent") and message.channel.parent is not None
            thread_id = str(message.channel.id) if is_thread else None

            if is_voice_chat_message:
                self._logger.info(
                    "Received Discord voice-channel chat message "
                    f"[message_id={message.id}, guild_id={guild_id}, channel_id={channel_id}, "
                    f"channel={getattr(message.channel, 'name', 'unknown')}, author_id={message.author.id}, "
                    f"channel_type={channel_type}, chars={len(message.content or '')}]"
                )
            else:
                self._logger.debug(
                    "Received Discord message "
                    f"[message_id={message.id}, guild_id={guild_id}, channel_id={channel_id}, "
                    f"channel_type={channel_type}, author_id={message.author.id}, "
                    f"chars={len(message.content or '')}]"
                )

            check_channel_id = channel_id
            if is_thread:
                inherit_perms = getattr(
                    self._chat_filter._config, "inherit_channel_permissions", True
                ) if self._chat_filter._config else True
                if inherit_perms and message.channel.parent:
                    check_channel_id = str(message.channel.parent.id)

            if not self._chat_filter.is_allowed(
                user_id=str(message.author.id),
                guild_id=guild_id,
                channel_id=check_channel_id,
                thread_id=thread_id,
                is_thread=is_thread,
            ):
                if is_voice_chat_message:
                    self._logger.warning(
                        "Ignored Discord voice-channel chat message because it was blocked by chat filter "
                        f"[message_id={message.id}, channel_id={channel_id}, check_channel_id={check_channel_id}]"
                    )
                return

            message_dict = await self._message_handler.handle_discord_message(message)
            if message_dict is None:
                return

            await self._gateway_capability.route_message(
                self._gateway_name,
                message_dict,
                external_message_id=str(message.id),
                dedupe_key=str(message.id),
            )

            routing_info = self._message_handler.get_thread_routing_info(message)
            if routing_info:
                if routing_info["is_inherit"]:
                    self._thread_routing_manager.update_thread_context(
                        routing_info["parent_channel_id"],
                        routing_info["thread_id"],
                    )
                elif not routing_info["is_thread"]:
                    pass
            elif message.guild and not is_thread:
                inherit_mem = getattr(
                    self._message_handler._chat_config, "inherit_channel_memory", True
                )
                if inherit_mem:
                    self._thread_routing_manager.clear_thread_context(str(message.channel.id))

        except Exception as exc:
            self._logger.error(f"处理 Discord 消息时发生错误: {exc}")
            self._logger.debug(traceback.format_exc())


    def _register_reaction_events(self) -> None:
        """为当前客户端实例注册 raw reaction 事件（去重，避免重复绑定）。"""
        client = self.client
        if client is None:
            return

        current_client_id = id(client)
        if self._registered_reaction_client_id == current_client_id:
            return

        @client.event
        async def on_raw_reaction_add(payload: discord.RawReactionActionEvent) -> None:
            await self._process_reaction_event("reaction_add", payload)

        @client.event
        async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent) -> None:
            await self._process_reaction_event("reaction_remove", payload)

        self._registered_reaction_client_id = current_client_id
        self._logger.info("Reaction 事件处理器已注册到 Discord 客户端")

    async def _process_reaction_event(
        self, event_type: str, payload: discord.RawReactionActionEvent
    ) -> None:
        """解析 Reaction 事件、按频道规则过滤后转换为 Host 消息并路由。

        Args:
            event_type: 事件类型标识（如 reaction_add / reaction_remove）。
            payload: Discord 提供的 `RawReactionActionEvent` 载荷。
        """
        try:
            if not self.client or not self.client.user:
                return
            if payload.user_id == self.client.user.id:
                return

            guild_id = str(payload.guild_id) if payload.guild_id else None
            channel_id = str(payload.channel_id)

            channel = self.client.get_channel(payload.channel_id)
            if not channel:
                try:
                    channel = await self.client.fetch_channel(payload.channel_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    return

            is_thread = isinstance(channel, discord.Thread)
            thread_id = channel_id if is_thread else None

            check_channel_id = channel_id
            if is_thread:
                inherit_perms = getattr(
                    self._chat_filter._config, "inherit_channel_permissions", True
                ) if self._chat_filter._config else True
                if inherit_perms:
                    parent = getattr(channel, "parent", None)
                    if parent:
                        check_channel_id = str(parent.id)

            if not self._chat_filter.is_allowed(
                user_id=str(payload.user_id),
                guild_id=guild_id,
                channel_id=check_channel_id,
                thread_id=thread_id,
                is_thread=is_thread,
            ):
                return

            message_dict = await self._message_handler.handle_reaction_event(
                event_type, payload, self.client
            )
            if message_dict is None:
                return

            unique_id = f"reaction_{payload.message_id}_{payload.user_id}_{event_type}"
            await self._gateway_capability.route_message(
                self._gateway_name,
                message_dict,
                external_message_id=unique_id,
                dedupe_key=unique_id,
            )

        except Exception as exc:
            self._logger.error(f"处理 {event_type} 事件时发生错误: {exc}")
            self._logger.debug(traceback.format_exc())


    async def start(self) -> None:
        """在关闭标志未置位时循环尝试启动客户端，使用 `client.start` 阻塞直至断开或致命错误。"""
        self._logger.info(f"正在启动 Discord 客户端... (重试间隔: {self._retry_delay}s)")

        attempt = 0
        while not self.is_shutting_down:
            try:
                if attempt > 0:
                    self._logger.info(f"第 {attempt} 次重试启动 Discord 客户端...")
                    await asyncio.sleep(self._retry_delay)
                    await self._reset_client()

                if self.client is None:
                    self._setup_client()
                await self.client.start(self._token)  # type: ignore[union-attr]
                self._logger.warning("Discord 客户端连接意外断开")
                break

            except (discord.LoginFailure, discord.HTTPException) as exc:
                error_text = str(exc).lower()
                if any(kw in error_text for kw in ("login", "token", "unauthorized")):
                    self._logger.error("Token 相关错误，请检查插件配置中的 connection.token")
                    raise
                self._logger.warning(f"第 {attempt + 1} 次尝试失败: {exc}")

            except (ConnectionError, TimeoutError, OSError) as exc:
                self._logger.warning(f"第 {attempt + 1} 次尝试失败: {exc}")

            except Exception as exc:
                self._logger.error(f"第 {attempt + 1} 次尝试时发生未知错误: {exc}")

            attempt += 1

    async def stop(self) -> None:
        """请求关闭：停止连接监控并关闭 Discord 客户端。"""
        self.is_shutting_down = True
        self._stop_monitor()

        if self.client and not self.client.is_closed():
            await self.client.close()
            self._logger.info("Discord 客户端已关闭")

    async def _reset_client(self) -> None:
        """关闭旧客户端（若存在）、清空 Reaction 注册标记并重新 `_setup_client`。"""
        if self.client and not self.client.is_closed():
            try:
                await self.client.close()
            except Exception:
                pass

        self._registered_reaction_client_id = None
        self._setup_client()
        self.is_connected = False

    async def force_reconnect(self) -> None:
        """在非关闭且非重连中时强制关闭当前连接并后台启动 `_reconnect_loop`。"""
        if self.is_shutting_down or self.is_reconnecting:
            return

        self._logger.info("强制重连 Discord 客户端...")
        self.is_reconnecting = True

        try:
            self.is_connected = False

            if self._reconnect_task and not self._reconnect_task.done():
                self._reconnect_task.cancel()
                try:
                    await self._reconnect_task
                except asyncio.CancelledError:
                    pass

            if self.client and not self.client.is_closed():
                try:
                    await asyncio.wait_for(self.client.close(), timeout=3.0)
                except asyncio.TimeoutError:
                    self._logger.warning("关闭 Discord 客户端超时，强制继续")

            await asyncio.sleep(0.5)
            await self._reset_client()
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

        except Exception as exc:
            self._logger.error(f"强制重连时发生错误: {exc}")
            self.is_connected = False
            self.is_reconnecting = False

    async def _reconnect_loop(self) -> None:
        """后台重连循环：在关闭前反复尝试 `client.start`，认证失败则退出并清除重连标志。"""
        try:
            attempt = 0
            while not self.is_shutting_down:
                try:
                    if attempt > 0:
                        await asyncio.sleep(self._retry_delay)
                        await self._reset_client()
                    if self.client is None:
                        self._setup_client()
                    await self.client.start(self._token)  # type: ignore[union-attr]
                    self.is_connected = False
                    attempt += 1
                except (discord.LoginFailure, discord.HTTPException) as exc:
                    self._logger.error(f"重连认证错误，停止: {exc}")
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._logger.warning(f"第 {attempt + 1} 次重连失败: {exc}")
                    attempt += 1
        except asyncio.CancelledError:
            pass
        finally:
            self.is_reconnecting = False


    def start_monitor(self) -> None:
        """若监控任务未在运行，则创建 `_connection_monitor_loop` 异步任务。"""
        if self._monitor_task is not None and not self._monitor_task.done():
            return
        self._monitor_task = asyncio.create_task(self._connection_monitor_loop())

    def _stop_monitor(self) -> None:
        """取消连接监控任务（若仍在运行）。"""
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()

    async def _connection_monitor_loop(self) -> None:
        """等待首次连接就绪后，按固定间隔调用连接状态检查直至关闭或取消。"""
        while not self.is_connected and not self.is_shutting_down:
            await asyncio.sleep(2)

        self._logger.info("Discord 连接监控已启动")

        while not self.is_shutting_down:
            try:
                await asyncio.sleep(self._connection_check_interval)
                if self.is_shutting_down:
                    break
                await self._check_connection_status()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._logger.error(f"连接监控异常: {exc}")

        self._logger.debug("连接监控已停止")

    async def _check_connection_status(self) -> None:
        """根据就绪状态与延迟判断连接是否健康，必要时恢复标志、执行主动健康检查或触发重连。"""
        client = self.client
        if client is None:
            return

        if client.is_closed():
            if self.is_connected:
                self.is_connected = False
            return

        try:
            is_ready, latency = await asyncio.wait_for(
                self._quick_check_ready(), timeout=3.0
            )

            latency_valid = (
                latency is not None
                and latency != float("inf")
                and latency == latency  # NaN check
                and latency >= 0
            )

            if is_ready and latency_valid and latency is not None and latency < 10.0:
                if not self.is_connected:
                    self._logger.info("Discord 连接已恢复")
                    self.is_connected = True
                    self._register_reaction_events()
                    if self._on_connected_callback:
                        try:
                            await self._on_connected_callback()
                        except Exception:
                            pass

                current_time = get_time()
                if current_time - self._last_health_check >= self._health_check_interval:
                    health_ok = await self._active_health_check()
                    self._last_health_check = current_time

                    if not health_ok:
                        self._health_check_failures += 1
                        self._logger.warning(
                            f"主动健康检查失败 ({self._health_check_failures}/3)"
                        )
                        if self._health_check_failures >= 3:
                            self._logger.error("检测到连接坏死，触发重连")
                            self._health_check_failures = 0
                            self.is_connected = False
                            await self.force_reconnect()
                    else:
                        self._health_check_failures = 0

            elif is_ready and (not latency_valid or (latency is not None and latency >= 10.0)):
                self._logger.warning(f"Discord 延迟异常: {latency}，等待自动恢复")
                self.is_connected = False

            else:
                if self.is_connected:
                    self.is_connected = False

        except asyncio.TimeoutError:
            self._logger.warning("连接状态检查超时")
            self.is_connected = False
        except Exception as exc:
            self._logger.error(f"检查连接状态异常: {exc}")
            self.is_connected = False

    async def _quick_check_ready(self) -> tuple[bool, Optional[float]]:
        """快速读取客户端是否就绪及当前 Gateway 延迟（毫秒级心跳延迟）。

        Returns:
            (是否就绪, 延迟秒数)；客户端不可用时返回 (False, None)。
        """
        try:
            client = self.client
            if client is None:
                return False, None
            return client.is_ready(), client.latency
        except Exception:
            return False, None

    async def _active_health_check(self) -> bool:
        """通过 `fetch_user` 验证当前登录用户是否仍可被 API 正常解析。

        Returns:
            检查通过为 True，超时或 HTTP/未知错误为 False。
        """
        try:
            if self.client and self.client.user:
                user = await asyncio.wait_for(
                    self.client.fetch_user(self.client.user.id), timeout=30.0
                )
                return user is not None
            return False
        except (asyncio.TimeoutError, discord.HTTPException, discord.NotFound):
            return False
        except Exception:
            return False
