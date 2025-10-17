"""模块名称：send_handler.main_send_handler
主要功能：负责将 MaiBot 消息调度到 Discord 频道或子频道。"""

from __future__ import annotations

import traceback

from typing import List, Optional, Sequence

import discord
from maim_message import BaseMessageInfo, MessageBase, Seg

from ..logger import logger
from .message_send_handler import DiscordContentBuilder
from .thread_send_handler import ThreadRoutingManager
from ..recv_handler.discord_client import discord_client

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

        self.MAX_MESSAGE_LENGTH = 2000 # pylint: disable=invalid-name
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
        except (ValueError, TypeError) as exc:
            logger.error(f"无法解析 MaiBot 消息对象：{exc}")
            return
        if not isinstance(message.message_info, BaseMessageInfo):
            logger.error(f"消息缺少有效的 message_info 字段：{message_dict}")
            return

        segment: Seg = message.message_segment
        if not isinstance(segment, Seg) or not getattr(segment, "type", None):
            logger.error(f"消息缺少有效的消息段信息：{message_dict}")
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
            None: 执行命令后无返回值。
        """
        segment = message.message_segment
        command_data = segment.data if hasattr(segment, 'data') else {}
        # 检查是否为 reaction 命令
        command_type = command_data.get('type', '')

        # 处理reaction命令
        if command_type == 'reaction':
            await self._handle_reaction_command(message, command_data)
        else:
            logger.warning(f"收到未知 command 消息类型: {command_type}, 数据: {command_data}")

    async def _handle_reaction_command(self, _message: MessageBase, command_data: dict) -> None:
        """处理 reaction 命令（添加或移除表情）
        
        Args:
            _message: MaiBot 消息对象
            command_data: 命令数据，包含:
                - action: 'add' 或 'remove'
                - message_id: 目标消息ID
                - channel_id: 频道ID
                - emoji: emoji字符串或名称
        
        Returns:
            None: 执行完成后无返回值
        """
        try:
            action = command_data.get('action', 'add')
            target_message_id = command_data.get('message_id')
            channel_id = command_data.get('channel_id')
            emoji_str = command_data.get('emoji')
            if not target_message_id or not channel_id or not emoji_str:
                logger.error(f"Reaction命令缺少必要参数，需要 message_id、channel_id 和 emoji: {command_data}")
                return

            logger.debug(
                f"处理reaction命令: action={action}, "
                f"message_id={target_message_id}, emoji={emoji_str}"
            )

            # 获取 Discord 客户端
            client = discord_client.client
            if not client or not getattr(client, 'user', None):
                logger.error("Discord客户端未就绪，无法执行reaction命令")
                return

            try:
                channel_id_int = int(channel_id)
                message_id_int = int(target_message_id)
            except (TypeError, ValueError) as e:
                logger.error(f"Reaction命令的消息或频道ID格式不正确: {command_data}, 错误: {e}")
                return

            # 获取频道
            channel = client.get_channel(channel_id_int)
            if not channel:
                try:
                    channel = await client.fetch_channel(channel_id_int)
                except (discord.NotFound, discord.Forbidden) as e:
                    logger.error(f"无法找到频道 {channel_id_int}: {e}")
                    return
                except discord.HTTPException as e:
                    logger.error(f"获取频道 {channel_id_int} 时发生错误: {e}")
                    return

            # 获取消息
            try:
                target_message = await channel.fetch_message(message_id_int)
            except discord.NotFound:
                logger.error(f"无法找到消息 {message_id_int}")
                return
            except discord.Forbidden:
                logger.error(f"没有权限访问消息 {message_id_int}")
                return
            except discord.HTTPException as e:
                logger.error(f"获取消息 {message_id_int} 时发生错误: {e}")
                return

            # 执行添加或移除 reaction
            if action == 'add':
                await target_message.add_reaction(emoji_str)
                logger.info(f"成功给消息 {message_id_int} 添加表情: {emoji_str}")
            elif action == 'remove':
                await target_message.remove_reaction(emoji_str, client.user)
                logger.info(f"成功从消息 {message_id_int} 移除表情: {emoji_str}")
            else:
                logger.warning(f"未知的reaction操作: {action}")
        except discord.HTTPException as e:
            logger.error(f"执行reaction命令时发生Discord HTTP错误: {e}")
            logger.error(f"错误详情: {traceback.format_exc()}")
        except (ValueError, AttributeError) as e:
            logger.error(f"执行reaction命令时发生参数错误: {e}")
            logger.error(f"错误详情: {traceback.format_exc()}")
        except Exception as e:
            logger.error(f"执行reaction命令时发生未知错误: {e}")
            logger.error(f"错误详情: {traceback.format_exc()}")

    async def _handle_notify(self, message: MessageBase) -> None:
        """处理 notify 类型消息，当前仅记录日志。

        Args:
            message: MaiBot 消息对象。

        Returns:
            None: 方法执行后无返回值。
        """

        logger.debug(f"收到 notify 消息，已忽略：{message.message_segment.data}")

    async def _handle_regular_message(self, message: MessageBase) -> None:
        """处理常规消息，将其发送到 Discord。

        Args:
            message: MaiBot 消息对象。

        Returns:
            None: 发送流程结束后无返回值。
        """

        message_info: BaseMessageInfo = message.message_info
        message_id: Optional[str] = getattr(message_info, "message_id", None)
        logger.debug(f"开始向 Discord 发送消息：{message_id}")

        target_channel: Optional[discord.abc.Messageable] = await self._thread_manager.resolve_target_channel(message)
        if target_channel is None:
            logger.warning(f"无法解析目标频道，放弃发送：{message_id}")
            return

        content_result: tuple[Optional[str], List[discord.File]] = self._content_builder.build(message.message_segment)
        content: Optional[str]
        files: List[discord.File]
        content, files = content_result
        content_preview: Optional[str] = None
        if content is not None:
            content_preview = content[:100] + "..." if len(content) > 100 else content
        files_count: int = len(files)
        logger.debug(f"消息内容预览：{content_preview}")
        logger.debug(f"附件数量：{files_count}")

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
            # Discord 限制：一条消息最多 10 个附件(主要指图片)
            MAX_FILES_PER_MESSAGE: int = 10 # pylint: disable=invalid-name

            # 如果有文件，检查数量并一次性发送
            if files:
                file_list: List[discord.File] = list(files)

                # 如果超过 10 个文件，只发送前 10 个并警告
                if len(file_list) > MAX_FILES_PER_MESSAGE:
                    logger.warning(f"消息包含 {len(file_list)} 个文件，超过 Discord 限制，仅发送前 {MAX_FILES_PER_MESSAGE} 个")
                    file_list = file_list[:MAX_FILES_PER_MESSAGE]

                # 一次性发送所有文件和文本内容
                send_content: Optional[str] = None
                if content and len(content) <= self.MAX_MESSAGE_LENGTH:
                    send_content = content
                    content = None  # 已使用，清空

                await channel.send(
                    content=send_content,
                    files=file_list,
                    reference=reference
                )
                logger.debug(f"已发送 {len(file_list)} 个文件" + ("和文本内容" if send_content else ""))

            # 如果还有剩余文本内容（太长或没有文件时），单独发送
            if content:
                if len(content) <= self.MAX_MESSAGE_LENGTH:
                    await channel.send(content=content, reference=reference if not files else None)
                else:
                    await self._send_long_message(channel, content, reference if not files else None)

        except (discord.HTTPException, discord.Forbidden) as exc:
            logger.error(f"发送 Discord 消息失败：{exc}")

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

        # 预留一些空间，避免边界问题
        max_len: int = self.MAX_MESSAGE_LENGTH - 10
        parts: List[str] = []

        # 检查是否有代码块
        if "```" in content:
            # 按代码块分割
            parts = self._split_preserve_codeblocks(content, max_len)
        else:
            # 按行分割
            parts = self._split_by_lines(content, max_len)

        # 发送所有部分
        for index, part in enumerate(parts):
            if part.strip():  # 跳过空白部分
                await self._send_single_part(
                    channel,
                    part,
                    reference if index == 0 else None
                )
                logger.debug(f"已发送长消息的第 {index + 1}/{len(parts)} 部分")

    def _split_preserve_codeblocks(self, content: str, max_len: int) -> List[str]:
        """保护代码块完整性的分割方法
        
        Args:
            content: 要分割的内容
            max_len: 每部分最大长度
            
        Returns:
            List[str]: 分割后的部分列表
        """
        parts: List[str] = []
        current: str = ""
        in_codeblock: bool = False
        codeblock_start: str = ""

        lines: List[str] = content.split("\n")

        for line in lines:
            # 检测代码块开始/结束
            if line.strip().startswith("```"):
                if not in_codeblock:
                    # 代码块开始
                    in_codeblock = True
                    codeblock_start = line.strip()
                else:
                    # 代码块结束
                    in_codeblock = False

            # 尝试添加当前行
            test_content: str = f"{current}\n{line}" if current else line

            if len(test_content) > max_len:
                # 超长了
                if in_codeblock:
                    # 在代码块中，需要关闭并重新开启
                    parts.append(current + "\n```")
                    current = codeblock_start + "\n" + line
                else:
                    # 不在代码块中，正常分割
                    if current:
                        parts.append(current)
                    current = line
            else:
                current = test_content

        # 添加最后一部分
        if current:
            parts.append(current)

        return parts

    def _split_by_lines(self, content: str, max_len: int) -> List[str]:
        """按行分割消息
        
        Args:
            content: 要分割的内容
            max_len: 每部分最大长度
            
        Returns:
            List[str]: 分割后的部分列表
        """
        parts: List[str] = []
        current: str = ""

        lines: List[str] = content.split("\n")

        for line in lines:
            # 如果单行就超长，强制切分
            if len(line) > max_len:
                if current:
                    parts.append(current)
                    current = ""

                # 切分超长行
                while len(line) > max_len:
                    parts.append(line[:max_len])
                    line = line[max_len:]

                current = line
            else:
                # 尝试添加当前行
                test_content: str = f"{current}\n{line}" if current else line

                if len(test_content) > max_len:
                    parts.append(current)
                    current = line
                else:
                    current = test_content

        # 添加最后一部分
        if current:
            parts.append(current)

        return parts

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
            logger.error(f"发送消息片段失败：{exc}")


send_handler = DiscordSendHandler()
