"""模块名称：send_handler.thread_send_headler
主要功能：管理 Discord 消息发送的频道与子区路由。"""

from __future__ import annotations

from typing import Any, List, Optional

import json

import discord
from maim_message import BaseMessageInfo, MessageBase, Seg

from ..config import global_config
from ..logger import logger
from ..recv_handler.discord_client import discord_client


class ThreadRoutingManager:
    """维护 Discord 子区上下文并解析发送目标。

    属性:
        _thread_context_map: 父频道到活跃子区的映射。
        _channel_cache: 频道缓存。
        _user_cache: 用户缓存。
    """

    _thread_context_map: dict[str, str]
    _channel_cache: dict[int, discord.abc.Messageable]
    _user_cache: dict[int, discord.User]

    def __init__(self) -> None:
        """初始化路由管理器。

        Returns:
            None: 初始化内部缓存后无返回值。
        """

        self._thread_context_map = {}
        self._channel_cache = {}
        self._user_cache = {}

    def update_thread_context(self, parent_channel_id: str, thread_id: str) -> None:
        """记录父频道与活跃子区的映射。

        Args:
            parent_channel_id: Discord 父频道标识。
            thread_id: 当前活跃子区标识。

        Returns:
            None: 更新映射后无返回值。
        """

        self._thread_context_map[parent_channel_id] = thread_id
        logger.debug("更新子区上下文映射：%s -> %s", parent_channel_id, thread_id)

    def clear_thread_context(self, parent_channel_id: str) -> None:
        """清除父频道映射关系。

        Args:
            parent_channel_id: Discord 父频道标识。

        Returns:
            None: 删除映射后无返回值。
        """

        previous_thread_id: Optional[str] = self._thread_context_map.pop(parent_channel_id, None)
        if previous_thread_id:
            logger.debug("清除子区上下文映射：%s (之前映射到 %s)", parent_channel_id, previous_thread_id)

    def get_active_thread(self, parent_channel_id: str) -> Optional[str]:
        """查询父频道当前记录的子区。

        Args:
            parent_channel_id: Discord 父频道标识。

        Returns:
            Optional[str]: 当前记录的子区标识，若不存在则为 None。
        """

        return self._thread_context_map.get(parent_channel_id)

    async def resolve_target_channel(self, message: MessageBase) -> Optional[discord.abc.Messageable]:
        """解析消息的发送目标频道。

        Args:
            message: MaiBot 消息对象。

        Returns:
            Optional[discord.abc.Messageable]: 解析到的频道或子区，失败时为 None。
        """

        message_info: Any = message.message_info
        if not isinstance(message_info, BaseMessageInfo):
            logger.error("消息缺少有效的 message_info：%s", message)
            return None

        if message_info.group_info:
            return await self._resolve_guild_target(message)

        user_info = message_info.user_info
        user_id: Optional[str] = getattr(user_info, "user_id", None) if user_info else None
        if not user_id:
            logger.error("消息缺少 user_id，无法解析私聊目标：%s", message)
            return None

        return await self._resolve_direct_target(user_id)

    async def get_reply_reference(
        self,
        message: MessageBase,
        channel: discord.abc.Messageable,
    ) -> Optional[discord.Message]:
        """获取回复消息引用。

        Args:
            message: MaiBot 消息对象。
            channel: 目标 Discord 频道或子区。

        Returns:
            Optional[discord.Message]: 被回复的消息对象，无法定位时为 None。
        """

        reply_id: Optional[str] = self._extract_reply_message_id(message.message_segment)
        if not reply_id:
            return None

        try:
            reply_int: int = int(reply_id)
        except (TypeError, ValueError):
            logger.warning("回复消息 ID 无效：%s", reply_id)
            return None

        try:
            if hasattr(channel, "get_partial_message"):
                partial_message = channel.get_partial_message(reply_int)
                return await partial_message.fetch()
            return await channel.fetch_message(reply_int)  # type: ignore[arg-type]
        except discord.NotFound:
            logger.warning("被回复的消息不存在：%s", reply_id)
        except discord.Forbidden:
            logger.warning("无权限获取被回复的消息：%s", reply_id)
        except discord.HTTPException as exc:
            logger.warning("获取被回复消息失败：%s", exc)

        return None

    async def _resolve_guild_target(self, message: MessageBase) -> Optional[discord.abc.Messageable]:
        """解析群组消息应发送的目标。

        Args:
            message: MaiBot 群组消息对象。

        Returns:
            Optional[discord.abc.Messageable]: 目标频道或子区，失败时为 None。
        """

        group_info = message.message_info.group_info
        if group_info is None:
            return None

        target_id: int = int(group_info.group_id)
        thread_routing: Optional[dict] = self._extract_thread_routing_info(message.message_segment)
        reply_id: Optional[str] = self._extract_reply_message_id(message.message_segment)
        reply_in_parent: bool = False

        if thread_routing:
            thread_channel: Optional[discord.abc.Messageable] = self._get_cached_channel(int(thread_routing["original_thread_id"]))
            if not thread_channel:
                fetched_thread: Optional[discord.abc.Messageable] = discord_client.client.get_channel(
                    int(thread_routing["original_thread_id"])
                )
                if fetched_thread:
                    thread_channel = fetched_thread
                    self._channel_cache[int(fetched_thread.id)] = fetched_thread
            if isinstance(thread_channel, discord.Thread):
                return thread_channel
            logger.warning("子区路由目标无效，回退到父频道：%s", thread_routing)

        if reply_id and global_config.chat.inherit_channel_memory:
            thread_from_reply = await self._find_thread_by_message_id(reply_id, target_id)
            if thread_from_reply:
                return thread_from_reply
            reply_in_parent = await self._reply_in_parent_channel(reply_id, target_id)

        channel: Optional[discord.abc.Messageable] = self._get_cached_channel(target_id)
        if not channel:
            fetched_channel: Optional[discord.abc.Messageable] = discord_client.client.get_channel(target_id)
            if fetched_channel:
                channel = fetched_channel
                self._channel_cache[target_id] = fetched_channel

        if channel is None:
            channel = await self._fetch_channel(target_id)

        if channel is None:
            logger.warning("找不到频道或子区：%s", target_id)
            return None

        if isinstance(channel, discord.Thread):
            if not channel.permissions_for(channel.guild.me).send_messages_in_threads:
                logger.warning("没有权限在子区 %s 发送消息", channel.id)
                return None
            return channel

        if isinstance(channel, discord.TextChannel):
            if not channel.permissions_for(channel.guild.me).send_messages:
                logger.warning("没有权限在频道 %s 发送消息", channel.id)
                return None

            if global_config.chat.inherit_channel_memory and not reply_in_parent:
                active_thread_id: Optional[str] = self.get_active_thread(str(target_id))
                if active_thread_id:
                    mapped_channel: Optional[discord.abc.Messageable] = self._get_cached_channel(int(active_thread_id))
                    if not mapped_channel:
                        mapped_channel = discord_client.client.get_channel(int(active_thread_id))
                    if isinstance(mapped_channel, discord.Thread):
                        return mapped_channel

            return channel

        logger.warning("目标 %s 不是文本频道也不是子区", target_id)
        return None

    async def _resolve_direct_target(self, user_id: str) -> Optional[discord.abc.Messageable]:
        """解析私聊消息的目标用户频道。

        Args:
            user_id: Discord 用户标识字符串。

        Returns:
            Optional[discord.abc.Messageable]: 对应的 DM 频道，失败时为 None。
        """

        try:
            int_user_id: int = int(user_id)
        except (TypeError, ValueError):
            logger.error("用户 ID 无效：%s", user_id)
            return None

        user: Optional[discord.User] = self._user_cache.get(int_user_id) or discord_client.client.get_user(int_user_id)
        if not user:
            try:
                user = await discord_client.client.fetch_user(int_user_id)
            except discord.NotFound:
                logger.warning("用户 %s 不存在", int_user_id)
                return None
            except discord.HTTPException as exc:
                logger.error("获取用户 %s 失败：%s", int_user_id, exc)
                return None

        self._user_cache[int_user_id] = user

        if user.dm_channel:
            return user.dm_channel

        try:
            return await user.create_dm()
        except discord.HTTPException as exc:
            logger.error("创建与用户 %s 的 DM 失败：%s", int_user_id, exc)
            return None

    def _get_cached_channel(self, channel_id: int) -> Optional[discord.abc.Messageable]:
        """从缓存中获取频道对象。

        Args:
            channel_id: Discord 频道标识。

        Returns:
            Optional[discord.abc.Messageable]: 缓存的频道实例，若不存在则为 None。
        """

        return self._channel_cache.get(channel_id)

    async def _fetch_channel(self, channel_id: int) -> Optional[discord.abc.Messageable]:
        """通过 API 获取频道信息并写入缓存。

        Args:
            channel_id: Discord 频道标识。

        Returns:
            Optional[discord.abc.Messageable]: 拉取到的频道实例，失败时为 None。
        """

        try:
            channel: discord.abc.Messageable = await discord_client.client.fetch_channel(channel_id)
        except discord.NotFound:
            return None
        except discord.Forbidden:
            logger.error("无权限访问频道 %s", channel_id)
            return None
        except discord.HTTPException as exc:
            logger.error("获取频道 %s 时出错：%s", channel_id, exc)
            return None

        self._channel_cache[channel_id] = channel
        return channel

    async def _reply_in_parent_channel(self, reply_id: str, parent_channel_id: int) -> bool:
        """判断被回复消息是否存在于父频道。

        Args:
            reply_id: 被回复消息标识。
            parent_channel_id: 父频道标识。

        Returns:
            bool: 若消息存在于父频道则为 True，否则为 False。
        """

        try:
            parent_channel: Optional[discord.abc.GuildChannel] = discord_client.client.get_channel(parent_channel_id)
            if not isinstance(parent_channel, discord.TextChannel):
                return False
            await parent_channel.fetch_message(int(reply_id))
            return True
        except (discord.NotFound, discord.Forbidden):
            return False
        except (discord.HTTPException, ValueError):
            return False

    async def _find_thread_by_message_id(self, message_id: str, parent_channel_id: int) -> Optional[discord.Thread]:
        """根据回复消息定位所在子区。

        Args:
            message_id: 被回复消息标识。
            parent_channel_id: 父频道标识。

        Returns:
            Optional[discord.Thread]: 包含对应消息的子区，未找到时为 None。
        """

        try:
            message_int: int = int(message_id)
        except (TypeError, ValueError):
            return None

        parent_channel: Optional[discord.abc.GuildChannel] = discord_client.client.get_channel(parent_channel_id)
        if not isinstance(parent_channel, discord.TextChannel):
            return None

        for thread in parent_channel.threads:
            thread_channel: discord.Thread = thread
            try:
                await thread_channel.fetch_message(message_int)
                return thread
            except (discord.NotFound, discord.Forbidden):
                continue
            except discord.HTTPException:
                continue

        try:
            archived_threads: List[discord.Thread] = [thread async for thread in parent_channel.archived_threads(limit=50)]
        except discord.HTTPException:
            archived_threads = []

        for thread in archived_threads:
            archived_thread: discord.Thread = thread
            try:
                await archived_thread.fetch_message(message_int)
                return thread
            except (discord.NotFound, discord.Forbidden):
                continue
            except discord.HTTPException:
                continue

        return None

    def _extract_reply_message_id(self, segment: Seg) -> Optional[str]:
        """从消息段中提取回复消息 ID。

        Args:
            segment: MaiBot 消息段。

        Returns:
            Optional[str]: 回复消息标识，找不到时为 None。
        """

        def extract(seg: Seg) -> Optional[str]:
            if not getattr(seg, "type", None):
                return None

            if seg.type == "reply":
                payload = seg.data
                if isinstance(payload, str):
                    normalized = self._normalize_dict(payload)
                    if normalized and normalized.get("message_id") is not None:
                        return str(normalized.get("message_id"))
                    return payload
                if isinstance(payload, dict):
                    message_id_value = payload.get("message_id")
                    return str(message_id_value) if message_id_value is not None else None
                if isinstance(payload, (int, float)):
                    return str(payload)
                if isinstance(payload, list):
                    for candidate in payload:
                        if isinstance(candidate, (str, int, float)):
                            return str(candidate)
                return None

            if seg.type == "seglist" and isinstance(seg.data, list):
                sub_segments: List[Any] = seg.data
                for sub_segment in sub_segments:
                    if isinstance(sub_segment, Seg):
                        result = extract(sub_segment)
                        if result:
                            return result
            return None

        return extract(segment)

    def _extract_thread_routing_info(self, segment: Seg) -> Optional[dict]:
        """从消息段中提取子区路由信息。

        Args:
            segment: MaiBot 消息段。

        Returns:
            Optional[dict]: 子区路由字典，未提供时为 None。
        """

        def extract(seg: Seg) -> Optional[dict]:
            if not getattr(seg, "type", None):
                return None

            if seg.type == "thread_context":
                normalized = self._normalize_dict(seg.data)
                return normalized
            if seg.type == "seglist" and isinstance(seg.data, list):
                sub_segments: List[Any] = seg.data
                for sub_segment in sub_segments:
                    if isinstance(sub_segment, Seg):
                        result = extract(sub_segment)
                        if result:
                            return result
            return None

        return extract(segment)

    @staticmethod
    def _normalize_dict(data: Any) -> Optional[dict]:
        """尝试将任意数据解析为字典。

        Args:
            data: 消息段携带的数据。

        Returns:
            Optional[dict]: 成功解析的字典，失败时为 None。
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
