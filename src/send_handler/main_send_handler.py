"""模块名称：send_handler.main_send_handler
主要功能：负责将 MaiBot 消息调度到 Discord 频道或子频道。"""

from __future__ import annotations

from typing import List, Optional, Sequence

import discord
from maim_message import BaseMessageInfo, MessageBase, Seg

from ..logger import logger
from .message_send_handler import DiscordContentBuilder
from .thread_send_handler import ThreadRoutingManager


class DiscordSendHandler:
    """MaiBot 到 Discord 的消息调度器。

    属性:
        MAX_MESSAGE_LENGTH: 单条文本允许的最大长度。
        _thread_manager: 子区与频道路由管理器。
        _content_builder: 消息内容构建器。
    """

    MAX_MESSAGE_LENGTH: int
    _thread_manager: ThreadRoutingManager
    _content_builder: DiscordContentBuilder

    def __init__(self) -> None:
        """初始化消息调度器。

        Returns:
            None: 方法执行完成后无返回值。
        """

        self.MAX_MESSAGE_LENGTH = 2000
        self._thread_manager = ThreadRoutingManager()
        self._content_builder = DiscordContentBuilder()

    def update_thread_context(self, parent_channel_id: str, thread_id: str) -> None:
        """记录父频道与活跃子区的关联关系。

        Args:
            parent_channel_id: Discord 父频道标识。
            thread_id: 当前活跃子区标识。

        Returns:
            None: 仅更新内部映射。
        """

        self._thread_manager.update_thread_context(parent_channel_id, thread_id)

    def clear_thread_context(self, parent_channel_id: str) -> None:
        """清除父频道的子区上下文记录。

        Args:
            parent_channel_id: Discord 父频道标识。

        Returns:
            None: 删除映射后无返回值。
        """

        self._thread_manager.clear_thread_context(parent_channel_id)

    def get_active_thread(self, parent_channel_id: str) -> Optional[str]:
        """查询父频道当前记录的活跃子区。

        Args:
            parent_channel_id: Discord 父频道标识。

        Returns:
            Optional[str]: 找到时返回子区标识，为空则返回 None。
        """

        return self._thread_manager.get_active_thread(parent_channel_id)

    async def handle_message(self, message_dict: dict) -> None:
        """接收来自 MaiBot Core 的消息并按类型处理。

        Args:
            message_dict: 来自核心的原始消息字典。

        Returns:
            None: 异步处理结束后无返回值。
        """

        try:
            message: MessageBase = MessageBase.from_dict(message_dict)
        except (TypeError, ValueError, KeyError) as exc:
            logger.error("解析 MaiBot 消息失败：%s", exc)
            return

        if not isinstance(message.message_info, BaseMessageInfo):
            logger.error("消息缺少有效的 message_info 字段：%s", message_dict)
            return

        segment: Seg = message.message_segment
        if not isinstance(segment, Seg) or not getattr(segment, "type", None):
            logger.error("消息缺少有效的消息段信息：%s", message_dict)
            return
        segment_type: Optional[str] = getattr(segment, "type", None)

        if segment_type == "command":
            await self._handle_command(message)
            return

        if segment_type == "notify":
            await self._handle_notify(message)
            return

        await self._handle_regular_message(message)

    async def _handle_command(self, message: MessageBase) -> None:
        """处理 command 类型消息。

        Args:
            message: MaiBot 消息对象。

        Returns:
            None: 当前仅记录日志，不做进一步处理。
        """

        logger.warning("收到 command 消息，Discord 适配器暂未实现命令处理：%s", message.message_segment.data)

    async def _handle_notify(self, message: MessageBase) -> None:
        """处理 notify 类型消息，当前仅记录日志。

        Args:
            message: MaiBot 消息对象。

        Returns:
            None: 方法执行后无返回值。
        """

        logger.debug("收到 notify 消息，已忽略：%s", message.message_segment.data)

    async def _handle_regular_message(self, message: MessageBase) -> None:
        """处理常规消息，将其发送到 Discord。

        Args:
            message: MaiBot 消息对象。

        Returns:
            None: 发送流程结束后无返回值。
        """

        message_info: BaseMessageInfo = message.message_info
        message_id: Optional[str] = getattr(message_info, "message_id", None)
        logger.debug("开始向 Discord 发送消息：%s", message_id)

        target_channel: Optional[discord.abc.Messageable] = await self._thread_manager.resolve_target_channel(message)
        if target_channel is None:
            logger.warning("无法解析目标频道，放弃发送：%s", message_id)
            return

        content_result: tuple[Optional[str], List[discord.File]] = self._content_builder.build(message.message_segment)
        content: Optional[str]
        files: List[discord.File]
        content, files = content_result
        content_preview: Optional[str] = None
        if content is not None:
            content_preview = content[:100] + "..." if len(content) > 100 else content
        files_count: int = len(files)
        logger.debug("消息内容预览：%s", content_preview)
        logger.debug("附件数量：%d", files_count)

        reference: Optional[discord.Message] = await self._thread_manager.get_reply_reference(message, target_channel)

        if not content and not files:
            logger.warning("消息内容为空且无附件，跳过发送")
            return

        await self._send_with_length_check(target_channel, content, files, reference)

    async def _send_with_length_check(
        self,
        channel: discord.abc.Messageable,
        content: Optional[str],
        files: Sequence[discord.File],
        reference: Optional[discord.Message],
    ) -> None:
        """根据长度限制发送文本与附件。

        Args:
            channel: 目标 Discord 频道或子区。
            content: 需要发送的文本内容，可为空。
            files: 需要发送的附件序列。
            reference: 回复引用消息，可为空。

        Returns:
            None: 所有消息发送完成后无返回值。
        """

        try:
            remaining_reference: Optional[discord.Message] = reference

            if files:
                for index, attachment in enumerate(files):
                    index_position: int = index
                    file_item: discord.File = attachment
                    payload_content: Optional[str] = None
                    payload_reference: Optional[discord.Message] = remaining_reference if index_position == 0 else None

                    if index_position == 0 and content and len(content) <= self.MAX_MESSAGE_LENGTH:
                        payload_content = content
                        content = None

                    await channel.send(content=payload_content, file=file_item, reference=payload_reference)

                remaining_reference = None

            if content:
                if len(content) <= self.MAX_MESSAGE_LENGTH:
                    await channel.send(content=content, reference=remaining_reference)
                else:
                    await self._send_long_message(channel, content, remaining_reference)

        except (discord.HTTPException, discord.Forbidden) as exc:
            logger.error("发送 Discord 消息失败：%s", exc)

    async def _send_long_message(
        self,
        channel: discord.abc.Messageable,
        content: str,
        reference: Optional[discord.Message],
    ) -> None:
        """拆分并发送超长文本消息。

        Args:
            channel: 目标 Discord 频道或子区。
            content: 超出长度限制的完整文本。
            reference: 回复引用消息，仅首条使用。

        Returns:
            None: 拆分发送完毕后无返回值。
        """

        lines: list[str] = content.split("\n")
        current: str = ""
        part_index: int = 0

        for line in lines:
            current_line: str = line
            if len(current_line) > self.MAX_MESSAGE_LENGTH:
                if current:
                    await self._send_single_part(channel, current, reference if part_index == 0 else None)
                    part_index += 1
                    current = ""

                remaining_line: str = current_line
                while len(remaining_line) > self.MAX_MESSAGE_LENGTH:
                    chunk: str = remaining_line[: self.MAX_MESSAGE_LENGTH]
                    remaining_line = remaining_line[self.MAX_MESSAGE_LENGTH :]
                    await self._send_single_part(channel, chunk, reference if part_index == 0 else None)
                    part_index += 1

                current = remaining_line
            else:
                candidate: str = f"{current}\n{current_line}" if current else current_line
                if len(candidate) > self.MAX_MESSAGE_LENGTH:
                    await self._send_single_part(channel, current, reference if part_index == 0 else None)
                    part_index += 1
                    current = current_line
                else:
                    current = candidate

        if current:
            await self._send_single_part(channel, current, reference if part_index == 0 else None)

    async def _send_single_part(
        self,
        channel: discord.abc.Messageable,
        content: str,
        reference: Optional[discord.Message],
    ) -> None:
        """发送单个文本片段并捕获异常。

        Args:
            channel: 目标 Discord 频道或子区。
            content: 当前待发送的文本片段。
            reference: 回复引用消息，仅首条传入。

        Returns:
            None: 发送完成后无返回值。
        """

        try:
            await channel.send(content=content, reference=reference)
        except (discord.HTTPException, discord.Forbidden) as exc:
            logger.error("发送消息片段失败：%s", exc)


send_handler: DiscordSendHandler = DiscordSendHandler()
