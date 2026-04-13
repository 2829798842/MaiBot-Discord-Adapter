"""Discord 子区路由管理器。"""

import json
from typing import Any, Dict, List, Optional

import discord
from maim_message import BaseMessageInfo, MessageBase, Seg


class ThreadRoutingManager:
    """维护 Discord 子区与父频道映射，并据消息元数据解析可发送频道或私聊目标。

    负责缓存频道/用户、根据 ``thread_context``、回复关系与配置项
    ``inherit_channel_memory`` 决定发到主频道、子区或 DM。
    """

    def __init__(self, logger: Any, chat_config: Any) -> None:
        """初始化路由管理器。

        Args:
            logger: 用于调试与错误输出的日志记录器。
            chat_config: 聊天相关配置（需含 ``inherit_channel_memory`` 等属性）。
        """
        self._logger = logger
        self._chat_config = chat_config
        self._thread_context_map: Dict[str, str] = {}
        self._channel_cache: Dict[int, discord.abc.Messageable] = {}
        self._user_cache: Dict[int, discord.User] = {}
        self._client: Optional[discord.Client] = None

    def bind_client(self, client: discord.Client) -> None:
        """绑定 Discord 客户端，后续解析频道与 DM 均依赖该实例。

        Args:
            client: 已就绪的 `discord.Client`。
        """

        self._client = client

    def update_config(self, chat_config: Any) -> None:
        """替换当前聊天配置（例如热更新后同步）。

        Args:
            chat_config: 新的聊天配置对象。
        """

        self._chat_config = chat_config

    def update_thread_context(self, parent_channel_id: str, thread_id: str) -> None:
        """记录「父文本频道 ID → 当前活跃子区 ID」映射，供同频道后续消息路由到子区。

        Args:
            parent_channel_id: 父文本频道的字符串形式 ID。
            thread_id: 子区（Thread）的字符串形式 ID。
        """
        self._thread_context_map[parent_channel_id] = thread_id
        self._logger.debug(f"更新子区上下文映射：{parent_channel_id} -> {thread_id}")

    def clear_thread_context(self, parent_channel_id: str) -> None:
        """移除指定父频道对应的子区活跃映射（若存在则打调试日志）。

        Args:
            parent_channel_id: 父文本频道的字符串形式 ID。
        """
        previous = self._thread_context_map.pop(parent_channel_id, None)
        if previous:
            self._logger.debug(f"清除子区上下文映射：{parent_channel_id} (之前映射到 {previous})")

    def get_active_thread(self, parent_channel_id: str) -> Optional[str]:
        """查询父频道当前映射到的子区 ID。

        Args:
            parent_channel_id: 父文本频道的字符串形式 ID。

        Returns:
            子区 ID 字符串；未映射时返回 None。
        """

        return self._thread_context_map.get(parent_channel_id)

    async def resolve_target_channel(self, message: MessageBase) -> Optional[discord.abc.Messageable]:
        """根据消息的群聊或私聊信息解析应发送到的可写频道或 DM 频道。

        Args:
            message: 含 `message_info` 与可选 `message_segment` 的 MaiBot 消息。

        Returns:
            `discord.abc.Messageable`；缺少有效信息、无客户端或解析失败时返回 None。
        """
        message_info: Any = message.message_info
        if not isinstance(message_info, BaseMessageInfo):
            self._logger.error(f"消息缺少有效的 message_info：{message}")
            return None

        additional_config = self._get_additional_config(message_info)

        target_group_id = self._normalize_route_id(additional_config.get("platform_io_target_group_id"))
        if target_group_id:
            return await self._resolve_guild_target(message, target_group_id=target_group_id)

        target_user_id = self._normalize_route_id(additional_config.get("platform_io_target_user_id"))
        if target_user_id:
            return await self._resolve_direct_target(target_user_id)

        if message_info.group_info:
            return await self._resolve_guild_target(message)

        user_info = message_info.user_info
        user_id: Optional[str] = getattr(user_info, "user_id", None) if user_info else None
        if not user_id:
            self._logger.error(f"消息缺少 user_id，无法解析私聊目标：{message}")
            return None
        return await self._resolve_direct_target(user_id)

    async def get_reply_reference(
        self, message: MessageBase, channel: discord.abc.Messageable
    ) -> Optional[discord.Message]:
        """从消息片段中提取被回复消息 ID，并在给定频道拉取完整 `discord.Message` 作为回复引用。

        Args:
            message: 可能含 ``reply`` 片段的 MaiBot 消息。
            channel: 发送目标频道（用于 `fetch_message` / `get_partial_message`）。

        Returns:
            被回复的 Discord 消息；无回复 ID、ID 无效、消息不存在或无权限时返回 None。
        """
        reply_id = self._extract_reply_message_id(message.message_segment)
        if not reply_id:
            return None
        try:
            reply_int = int(reply_id)
        except (TypeError, ValueError):
            self._logger.warning(f"回复消息 ID 无效：{reply_id}")
            return None
        try:
            if hasattr(channel, "get_partial_message"):
                return await channel.get_partial_message(reply_int).fetch()
            return await channel.fetch_message(reply_int)  # type: ignore[arg-type]
        except discord.NotFound:
            self._logger.warning(f"被回复的消息不存在：{reply_id}")
        except discord.Forbidden:
            self._logger.warning(f"无权限获取被回复的消息：{reply_id}")
        except discord.HTTPException as exc:
            self._logger.warning(f"获取被回复消息失败：{exc}")
        return None

    async def _resolve_guild_target(
        self, message: MessageBase, target_group_id: Optional[str] = None
    ) -> Optional[discord.abc.Messageable]:
        """解析群聊消息的发送目标：优先子区路由与回复所在子区，再按配置回落到父频道或活跃子区。

        Args:
            message: 含 `group_info` 与片段树的 MaiBot 消息。

        Returns:
            目标 `TextChannel`、`Thread`、`VoiceChannel`、`StageChannel` 或其他可写对象；
            无客户端、无权限或类型不符时返回 None。
        """
        if self._client is None:
            return None

        group_info = message.message_info.group_info
        raw_target_id = target_group_id
        if not raw_target_id and group_info is not None:
            raw_target_id = self._normalize_route_id(getattr(group_info, "group_id", None))
        if not raw_target_id:
            self._logger.warning("Guild outbound message is missing a usable target channel id")
            return None

        try:
            target_id = int(raw_target_id)
        except (TypeError, ValueError):
            self._logger.warning(f"Invalid guild target channel id: {raw_target_id}")
            return None
        thread_routing = self._extract_thread_routing_info(message.message_segment)
        reply_id = self._extract_reply_message_id(message.message_segment)
        reply_in_parent = False
        inherit = getattr(self._chat_config, "inherit_channel_memory", True)

        if thread_routing:
            thread_channel = self._get_cached_channel(int(thread_routing["original_thread_id"]))
            if not thread_channel:
                fetched = self._client.get_channel(int(thread_routing["original_thread_id"]))
                if fetched:
                    thread_channel = fetched
                    self._channel_cache[int(fetched.id)] = fetched
            if isinstance(thread_channel, discord.Thread):
                return thread_channel
            self._logger.warning(f"子区路由目标无效，回退到父频道：{thread_routing}")

        if reply_id and inherit:
            thread_from_reply = await self._find_thread_by_message_id(reply_id, target_id)
            if thread_from_reply:
                return thread_from_reply
            reply_in_parent = await self._reply_in_parent_channel(reply_id, target_id)

        channel = self._get_cached_channel(target_id)
        if not channel:
            fetched = self._client.get_channel(target_id)
            if fetched:
                channel = fetched
                self._channel_cache[target_id] = fetched

        if channel is None:
            channel = await self._fetch_channel(target_id)

        if channel is None:
            self._logger.warning(f"找不到频道或子区：{target_id}")
            return None

        if isinstance(channel, discord.Thread):
            if not channel.permissions_for(channel.guild.me).send_messages_in_threads:
                self._logger.warning(f"没有权限在子区 {channel.id} 发送消息")
                return None
            return channel

        if isinstance(channel, discord.TextChannel):
            if not channel.permissions_for(channel.guild.me).send_messages:
                self._logger.warning(f"没有权限在频道 {channel.id} 发送消息")
                return None

            if inherit and not reply_in_parent:
                active_thread_id = self.get_active_thread(str(target_id))
                if active_thread_id:
                    mapped = self._get_cached_channel(int(active_thread_id))
                    if not mapped:
                        mapped = self._client.get_channel(int(active_thread_id))
                    if isinstance(mapped, discord.Thread):
                        return mapped
            return channel

        if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            if not channel.permissions_for(channel.guild.me).send_messages:
                self._logger.warning(f"没有权限在语音频道聊天区 {channel.id} 发送消息")
                return None
            return channel

        self._logger.warning(
            f"目标 {target_id} 不是可发送消息的文本/子区/语音聊天频道，而是 {type(channel).__name__}"
        )
        return None

    async def _resolve_direct_target(self, user_id: str) -> Optional[discord.abc.Messageable]:
        """将用户 ID 解析为私聊 DM 频道（带用户缓存与按需 `fetch_user` / `create_dm`）。

        Args:
            user_id: 目标用户的字符串形式 ID。

        Returns:
            DM 频道；用户不存在、无法创建 DM 或无客户端时返回 None。
        """
        if self._client is None:
            return None
        try:
            int_user_id = int(user_id)
        except (TypeError, ValueError):
            self._logger.error(f"用户 ID 无效：{user_id}")
            return None

        user = self._user_cache.get(int_user_id) or self._client.get_user(int_user_id)
        if not user:
            try:
                user = await self._client.fetch_user(int_user_id)
            except discord.NotFound:
                self._logger.warning(f"用户 {int_user_id} 不存在")
                return None
            except discord.HTTPException as exc:
                self._logger.error(f"获取用户 {int_user_id} 失败：{exc}")
                return None

        self._user_cache[int_user_id] = user
        if user.dm_channel:
            return user.dm_channel
        try:
            return await user.create_dm()
        except discord.HTTPException as exc:
            self._logger.error(f"创建与用户 {int_user_id} 的 DM 失败：{exc}")
            return None

    @staticmethod
    def _get_additional_config(message_info: BaseMessageInfo) -> Dict[str, Any]:
        """Safely read ``message_info.additional_config`` as a dict."""
        additional_config = getattr(message_info, "additional_config", None)
        return additional_config if isinstance(additional_config, dict) else {}

    @staticmethod
    def _normalize_route_id(value: Any) -> Optional[str]:
        """Normalize a route id to a non-empty string."""
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    def _get_cached_channel(self, channel_id: int) -> Optional[discord.abc.Messageable]:
        """从本地频道缓存读取已解析过的频道对象。

        Args:
            channel_id: Discord 频道数字 ID。

        Returns:
            缓存的 `Messageable`；未命中时返回 None。
        """

        return self._channel_cache.get(channel_id)

    async def _fetch_channel(self, channel_id: int) -> Optional[discord.abc.Messageable]:
        """通过 API 拉取频道并写入缓存；用于 `get_channel` 未命中时。

        Args:
            channel_id: Discord 频道数字 ID。

        Returns:
            频道对象；不存在、无权限或请求失败时返回 None。
        """
        if self._client is None:
            return None
        try:
            channel = await self._client.fetch_channel(channel_id)
        except discord.NotFound:
            return None
        except discord.Forbidden:
            self._logger.error(f"无权限访问频道 {channel_id}")
            return None
        except discord.HTTPException as exc:
            self._logger.error(f"获取频道 {channel_id} 时出错：{exc}")
            return None
        self._channel_cache[channel_id] = channel
        return channel

    async def _reply_in_parent_channel(self, reply_id: str, parent_channel_id: int) -> bool:
        """判断被回复消息是否位于父文本频道（而非子区内），用于决定是否继承子区上下文。

        Args:
            reply_id: 被回复消息的字符串 ID。
            parent_channel_id: 父文本频道的数字 ID。

        Returns:
            若在父频道能成功 `fetch_message` 则为 True，否则为 False。
        """
        if self._client is None:
            return False
        try:
            parent = self._client.get_channel(parent_channel_id)
            if not isinstance(parent, discord.TextChannel):
                return False
            await parent.fetch_message(int(reply_id))
            return True
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
            return False

    async def _find_thread_by_message_id(
        self, message_id: str, parent_channel_id: int
    ) -> Optional[discord.Thread]:
        """在父文本频道的活跃与已归档子区中查找包含指定消息 ID 的子区。

        Args:
            message_id: 要查找的 Discord 消息 ID。
            parent_channel_id: 父 `TextChannel` 的数字 ID。

        Returns:
            包含该消息的 `discord.Thread`；未找到或父级非文本频道时返回 None。
        """
        if self._client is None:
            return None
        try:
            message_int = int(message_id)
        except (TypeError, ValueError):
            return None

        parent = self._client.get_channel(parent_channel_id)
        if not isinstance(parent, discord.TextChannel):
            return None

        for thread in parent.threads:
            try:
                await thread.fetch_message(message_int)
                return thread
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                continue

        try:
            archived = [t async for t in parent.archived_threads(limit=50)]
        except discord.HTTPException:
            archived = []

        for thread in archived:
            try:
                await thread.fetch_message(message_int)
                return thread
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                continue

        return None

    def _extract_reply_message_id(self, segment: Seg) -> Optional[str]:
        """自片段树深度优先提取首个 ``reply`` 片段中的被回复消息 ID。

        Args:
            segment: 根 Seg，可含嵌套 `seglist`。

        Returns:
            消息 ID 字符串；无回复片段或无法解析时返回 None。
        """

        def extract(seg: Seg) -> Optional[str]:
            if not getattr(seg, "type", None):
                return None
            if seg.type == "reply":
                payload = seg.data
                if isinstance(payload, str):
                    normalized = self._normalize_dict(payload)
                    if normalized and normalized.get("message_id") is not None:
                        return str(normalized["message_id"])
                    return payload
                if isinstance(payload, dict):
                    mid = payload.get("message_id")
                    return str(mid) if mid is not None else None
                if isinstance(payload, (int, float)):
                    return str(payload)
                if isinstance(payload, list):
                    for candidate in payload:
                        if isinstance(candidate, (str, int, float)):
                            return str(candidate)
                return None
            if seg.type == "seglist" and isinstance(seg.data, list):
                for sub in seg.data:
                    if isinstance(sub, Seg):
                        result = extract(sub)
                        if result:
                            return result
            return None

        return extract(segment)

    def _extract_thread_routing_info(self, segment: Seg) -> Optional[Dict[str, Any]]:
        """自片段树中提取首个 ``thread_context`` 载荷（规范化后的字典）。

        Args:
            segment: 根 Seg，可含嵌套 `seglist`。

        Returns:
            子区路由信息字典；无 `thread_context` 时返回 None。
        """

        def extract(seg: Seg) -> Optional[Dict[str, Any]]:
            if not getattr(seg, "type", None):
                return None
            if seg.type == "thread_context":
                return self._normalize_dict(seg.data)
            if seg.type == "seglist" and isinstance(seg.data, list):
                for sub in seg.data:
                    if isinstance(sub, Seg):
                        result = extract(sub)
                        if result:
                            return result
            return None

        return extract(segment)

    @staticmethod
    def _normalize_dict(data: Any) -> Optional[Dict[str, Any]]:
        """将 dict 或 JSON 字符串规范化为字典；与 `thread_context` / 回复载荷解析共用。

        Args:
            data: 字典、JSON 字符串或其它类型。

        Returns:
            字典；无法解析或顶层非对象时返回 None。
        """
        if isinstance(data, dict):
            return data
        if isinstance(data, str):
            try:
                loaded = json.loads(data)
            except json.JSONDecodeError:
                return None
            return loaded if isinstance(loaded, dict) else None
        return None
