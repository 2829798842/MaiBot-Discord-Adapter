"""Discord 入站消息编解码器。

将 Discord 消息/事件转换为 Host 侧 message_dict 结构。
"""

import base64
import re
import time
import traceback
from typing import Any, Dict, List, Optional

import discord
from maim_message import BaseMessageInfo, FormatInfo, GroupInfo, MessageBase, Seg, UserInfo

from .emoji_mapping import format_reaction_for_ai, get_emoji_meaning


class DiscordMessageHandler:
    """Discord 入站消息处理器。

    将 Discord 消息转换为 MaiBot Host 侧的 message_dict 结构。
    不直接与 gateway 交互，仅负责转换。
    """

    def __init__(self, logger: Any, platform_name: str, chat_config: Any) -> None:
        """创建处理器并保存平台名与聊天相关配置。

        Args:
            logger: 日志记录器。
            platform_name: 写入 `MessageBase` 等平台字段的平台标识。
            chat_config: 聊天/子区记忆继承等行为的配置对象。
        """
        self._logger = logger
        self._platform_name = platform_name
        self._chat_config = chat_config

    def update_config(self, platform_name: str, chat_config: Any) -> None:
        """热更新平台名与聊天配置（不改变已缓存的其他状态）。

        Args:
            platform_name: 新的平台标识。
            chat_config: 新的聊天配置对象。
        """
        self._platform_name = platform_name
        self._chat_config = chat_config

    async def handle_discord_message(self, message: discord.Message) -> Optional[Dict[str, Any]]:
        """将 Discord 消息转换为 Host 侧 `message_dict`。

        Args:
            message: Discord 入站 `Message` 对象。

        Returns:
            转换成功返回字典；异常或无可序列化内容时返回 None。
        """
        try:
            maim_message = await self._convert_discord_to_maim(message)
            if maim_message is None:
                return None
            return self.build_host_message_dict(maim_message)
        except Exception as exc:
            self._logger.error(f"处理 Discord 消息时发生错误: {exc}")
            self._logger.debug(traceback.format_exc())
            return None

    async def handle_reaction_event(
        self,
        event_type: str,
        payload: discord.RawReactionActionEvent,
        client: discord.Client,
    ) -> Optional[Dict[str, Any]]:
        """将 Reaction 原始事件转换为 Host 侧 `message_dict`。

        Args:
            event_type: 事件类型（如 reaction_add / reaction_remove）。
            payload: Discord `RawReactionActionEvent` 载荷。
            client: 当前 Bot 客户端，用于拉取用户与频道信息。

        Returns:
            转换成功返回字典；异常或无法构建时返回 None。
        """
        try:
            maim_message = await self._convert_reaction_to_maim(event_type, payload, client)
            if maim_message is None:
                return None
            return self.build_host_message_dict(maim_message)
        except Exception as exc:
            self._logger.error(f"转换 {event_type} 事件时发生错误: {exc}")
            self._logger.debug(traceback.format_exc())
            return None

    def build_host_message_dict(self, maim_message: MessageBase) -> Dict[str, Any]:
        """将 `MessageBase` 转换为 Host 运行时要求的 `message_dict` 结构。"""
        message_info = maim_message.message_info
        if not isinstance(message_info, BaseMessageInfo):
            raise ValueError("MessageBase 缺少有效的 message_info")

        user_info = getattr(message_info, "user_info", None)
        user_id = str(getattr(user_info, "user_id", "") or "").strip()
        user_nickname = str(getattr(user_info, "user_nickname", "") or user_id).strip() or user_id
        user_cardname = getattr(user_info, "user_cardname", None)
        if not user_id:
            raise ValueError("MessageBase 缺少有效的 user_info.user_id")

        raw_message = self._serialize_message_segment(getattr(maim_message, "message_segment", None))
        if not raw_message:
            raw_text = self._normalize_text(getattr(maim_message, "raw_message", None))
            if raw_text:
                raw_message = [{"type": "text", "data": raw_text}]

        traits = self._analyze_segment_tree(getattr(maim_message, "message_segment", None))
        processed_plain_text = self._build_processed_plain_text(raw_message)
        if not processed_plain_text:
            processed_plain_text = self._normalize_text(getattr(maim_message, "raw_message", None))

        message_id = str(getattr(message_info, "message_id", "") or "").strip()
        if not message_id:
            message_id = f"discord-{int(time.time() * 1000)}"

        timestamp_value = getattr(message_info, "time", time.time())
        try:
            timestamp = str(float(timestamp_value))
        except (TypeError, ValueError):
            timestamp = str(float(time.time()))

        existing_additional_config = getattr(message_info, "additional_config", None)
        additional_config: Dict[str, Any] = (
            dict(existing_additional_config)
            if isinstance(existing_additional_config, dict)
            else {}
        )

        message_info_dict: Dict[str, Any] = {
            "user_info": {
                "user_id": user_id,
                "user_nickname": user_nickname,
                "user_cardname": str(user_cardname) if user_cardname else None,
            },
            "additional_config": additional_config,
        }

        group_info = getattr(message_info, "group_info", None)
        if isinstance(group_info, GroupInfo):
            group_id = str(getattr(group_info, "group_id", "") or "").strip()
            group_name = str(getattr(group_info, "group_name", "") or group_id).strip() or group_id
            if group_id:
                message_info_dict["group_info"] = {
                    "group_id": group_id,
                    "group_name": group_name,
                }
                additional_config["platform_io_target_group_id"] = group_id
        else:
            additional_config["platform_io_target_user_id"] = user_id

        message_dict: Dict[str, Any] = {
            "message_id": message_id,
            "timestamp": timestamp,
            "platform": str(getattr(message_info, "platform", "") or self._platform_name),
            "message_info": message_info_dict,
            "raw_message": raw_message,
            "is_mentioned": traits["is_mentioned"],
            "is_at": traits["is_at"],
            "is_emoji": traits["is_emoji"],
            "is_picture": traits["is_picture"],
            "is_command": bool(processed_plain_text) and processed_plain_text.lstrip().startswith("/"),
            "is_notify": False,
            "session_id": "",
            "processed_plain_text": processed_plain_text,
            "display_message": processed_plain_text,
        }

        if traits["reply_to"]:
            message_dict["reply_to"] = traits["reply_to"]

        return message_dict

    def get_thread_routing_info(self, message: discord.Message) -> Optional[Dict[str, Any]]:
        """从 Discord 消息中提取子区（线程）路由元数据，供网关更新线程上下文。

        Args:
            message: 已判定可能位于子区内的 `Message`（通常由调用方与频道类型配合使用）。

        Returns:
            在公会内且频道为子区时返回包含 `parent_channel_id`、`thread_id`、`is_thread`、`is_inherit` 的字典；
            非公会或非子区消息返回 None。
        """
        if not message.guild:
            return None
        is_thread = hasattr(message.channel, "parent") and message.channel.parent is not None
        if not is_thread:
            return None

        inherit = getattr(self._chat_config, "inherit_channel_memory", True)
        return {
            "is_thread": True,
            "parent_channel_id": str(message.channel.parent.id),
            "thread_id": str(message.channel.id),
            "is_inherit": inherit,
        }


    def _serialize_message_segment(self, segment: Any) -> List[Dict[str, Any]]:
        """将适配器内部 `Seg` 树拍平成 Host 能识别的标准消息片段列表。"""
        serialized: List[Dict[str, Any]] = []

        def walk(seg: Any) -> None:
            if not isinstance(seg, Seg) or not getattr(seg, "type", None):
                return

            if seg.type == "seglist" and isinstance(seg.data, list):
                for sub in seg.data:
                    walk(sub)
                return

            if seg.type == "text":
                text = self._normalize_text(seg.data)
                if text:
                    serialized.append({"type": "text", "data": text})
                return

            if seg.type in {"image", "emoji", "voice"}:
                binary_component = self._build_binary_component(seg.type, seg.data)
                if binary_component is not None:
                    serialized.append(binary_component)
                else:
                    placeholder_map = {
                        "image": "[图片]",
                        "emoji": "[表情]",
                        "voice": "[语音]",
                    }
                    serialized.append({"type": "text", "data": placeholder_map[seg.type]})
                return

            if seg.type == "reply":
                reply_id = self._normalize_text(seg.data)
                if reply_id:
                    serialized.append(
                        {
                            "type": "reply",
                            "data": {
                                "target_message_id": reply_id,
                            },
                        }
                    )
                return

            if seg.type == "video":
                serialized.append({"type": "text", "data": "[视频]"})
                return

            if seg.type == "file":
                serialized.append({"type": "text", "data": "[文件]"})
                return

        walk(segment)
        return serialized

    def _build_binary_component(self, component_type: str, data: Any) -> Optional[Dict[str, Any]]:
        """为图片/表情/语音片段构造 Host 标准二进制组件。"""
        if not isinstance(data, str) or not data:
            return None
        return {
            "type": component_type,
            "data": "",
            "binary_data_base64": data,
        }

    def _build_processed_plain_text(self, raw_message: List[Dict[str, Any]]) -> str:
        """根据标准 raw_message 片段构建可展示的纯文本内容。"""
        parts: List[str] = []
        for component in raw_message:
            if not isinstance(component, dict):
                continue
            component_type = str(component.get("type") or "").strip()
            if component_type == "text":
                parts.append(self._normalize_text(component.get("data")))
                continue
            if component_type == "image":
                parts.append("[图片]")
                continue
            if component_type == "emoji":
                parts.append("[表情]")
                continue
            if component_type == "voice":
                parts.append("[语音]")
                continue
        return "".join(part for part in parts if part).strip()

    def _analyze_segment_tree(self, segment: Any) -> Dict[str, Any]:
        """从原始 `Seg` 树中提取触发标记与回复目标等元信息。"""
        traits: Dict[str, Any] = {
            "is_mentioned": False,
            "is_at": False,
            "is_emoji": False,
            "is_picture": False,
            "reply_to": None,
        }

        def walk(seg: Any) -> None:
            if not isinstance(seg, Seg) or not getattr(seg, "type", None):
                return

            if seg.type == "seglist" and isinstance(seg.data, list):
                for sub in seg.data:
                    walk(sub)
                return

            if seg.type == "mention" and isinstance(seg.data, dict):
                if seg.data.get("users") or seg.data.get("everyone") or seg.data.get("here"):
                    traits["is_mentioned"] = True
                    traits["is_at"] = True
                return

            if seg.type == "image":
                traits["is_picture"] = True
                return

            if seg.type == "emoji":
                traits["is_emoji"] = True
                return

            if seg.type == "reply" and traits["reply_to"] is None:
                reply_id = self._normalize_text(seg.data)
                if reply_id:
                    traits["reply_to"] = reply_id

        walk(segment)
        return traits

    @staticmethod
    def _normalize_text(value: Any) -> str:
        """将任意值规整为字符串；空值返回空串。"""
        if value is None:
            return ""
        normalized = str(value)
        return normalized if normalized else ""

    async def _convert_discord_to_maim(self, message: discord.Message) -> Optional[MessageBase]:
        """将单条 Discord 消息装配为 `MessageBase`（用户、群组、分段、引用与线程上下文）。

        Args:
            message: Discord `Message`。

        Returns:
            成功时返回 `MessageBase`；无有效分段或出错时返回 None。
        """
        try:
            user_info = self._build_user_info(message.author)

            group_info = None
            additional_config: Dict[str, Any] = {}
            thread_context_marker = False
            original_thread_id: Optional[str] = None
            thread_name: Optional[str] = None

            if message.guild:
                is_thread = hasattr(message.channel, "parent") and message.channel.parent is not None

                if is_thread:
                    thread_name = message.channel.name
                    parent_channel_name = (
                        message.channel.parent.name
                        if hasattr(message.channel.parent, "name")
                        else f"频道{message.channel.parent.id}"
                    )
                    inherit = getattr(self._chat_config, "inherit_channel_memory", True)

                    if inherit:
                        group_id = str(message.channel.parent.id)
                        group_name = f"{parent_channel_name} @ {message.guild.name}"
                        actual_context = f"[当前子区: {thread_name}] {group_name}"
                        group_info = GroupInfo(
                            platform=self._platform_name,
                            group_id=group_id,
                            group_name=actual_context,
                        )
                        thread_context_marker = True
                        original_thread_id = str(message.channel.id)
                    else:
                        group_id = str(message.channel.id)
                        group_name = f"{thread_name} [{parent_channel_name}] @ {message.guild.name}"
                        group_info = GroupInfo(
                            platform=self._platform_name,
                            group_id=group_id,
                            group_name=group_name,
                        )
                else:
                    channel_name = (
                        message.channel.name
                        if hasattr(message.channel, "name")
                        else f"频道{message.channel.id}"
                    )
                    group_name = f"{channel_name} @ {message.guild.name}"
                    group_info = GroupInfo(
                        platform=self._platform_name,
                        group_id=str(message.channel.id),
                        group_name=group_name,
                    )
                    if isinstance(message.channel, discord.VoiceChannel):
                        additional_config["discord_channel_type"] = "voice"
                    elif isinstance(message.channel, discord.StageChannel):
                        additional_config["discord_channel_type"] = "stage"
                    elif isinstance(message.channel, discord.TextChannel):
                        additional_config["discord_channel_type"] = "text"

            message_segments: List[Seg] = []
            content_formats: List[str] = []

            mentions_info = self._process_mentions(message)
            if mentions_info:
                message_segments.append(Seg(type="mention", data=mentions_info))
                if "mention" not in content_formats:
                    content_formats.append("mention")

            if message.content:
                processed = self._process_text_with_emojis(message.content, message)
                message_segments.extend(processed)
                for seg in processed:
                    if seg.type not in content_formats:
                        content_formats.append(seg.type)

            for attachment in message.attachments:
                if attachment.content_type and attachment.content_type.startswith("image/"):
                    try:
                        image_data = await attachment.read()
                        image_base64 = base64.b64encode(image_data).decode("utf-8")
                        message_segments.append(Seg(type="image", data=image_base64))
                        content_formats.append("image")
                    except (discord.HTTPException, discord.NotFound, OSError) as exc:
                        self._logger.error(f"处理图片附件失败: {exc}")

            for sticker in message.stickers:
                sticker_text = f"[贴纸: {sticker.name}]"
                if not message.content:
                    message_segments.append(Seg(type="text", data=sticker_text))
                    if "text" not in content_formats:
                        content_formats.append("text")
                elif message_segments and message_segments[-1].type == "text":
                    message_segments[-1].data += f" {sticker_text}"

            if message.reference and message.reference.message_id:
                reply_message_id = str(message.reference.message_id)
                reply_context = await self._get_reply_context(message)
                if reply_context:
                    message_segments.insert(0, Seg(type="text", data=reply_context))
                message_segments.append(Seg(type="reply", data=reply_message_id))
                if "reply" not in content_formats:
                    content_formats.append("reply")

            if thread_context_marker and original_thread_id:
                thread_routing_info = {
                    "original_thread_id": original_thread_id,
                    "thread_name": thread_name,
                    "parent_channel_id": str(message.channel.parent.id),
                    "inherit_memory": True,
                }
                message_segments.append(Seg(type="thread_context", data=thread_routing_info))

            if not message_segments:
                return None

            format_info = FormatInfo(
                content_format=content_formats if content_formats else ["text"],
                accept_format=["text", "image", "emoji", "reply", "voice", "command", "file", "video"],
            )

            message_info = BaseMessageInfo(
                platform=self._platform_name,
                message_id=str(message.id),
                time=message.created_at.timestamp(),
                user_info=user_info,
                group_info=group_info,
                format_info=format_info,
                additional_config=additional_config or None,
            )

            if len(message_segments) == 1:
                message_segment = message_segments[0]
            else:
                message_segment = Seg(type="seglist", data=message_segments)

            return MessageBase(
                message_info=message_info,
                message_segment=message_segment,
                raw_message=message.content or "",
            )

        except Exception as exc:
            self._logger.error(f"转换 Discord 消息时发生错误: {exc}")
            return None

    def _build_user_info(self, author: discord.User | discord.Member) -> UserInfo:
        """从作者对象构造 Host `UserInfo`（展示名与服务器内昵称）。

        Args:
            author: 消息作者，可为 `User` 或 `Member`。

        Returns:
            包含平台、用户 ID、昵称与服务器卡片的 `UserInfo`。
        """
        display_name = author.display_name
        server_nickname = getattr(author, "nick", None)
        return UserInfo(
            platform=self._platform_name,
            user_id=str(author.id),
            user_nickname=display_name,
            user_cardname=server_nickname,
        )

    def _process_mentions(self, message: discord.Message) -> Optional[Dict[str, Any]]:
        """汇总消息中的 @用户、@角色、@频道及 @everyone/@here 信息为结构化字典。

        Args:
            message: 含 mentions 的 Discord `Message`。

        Returns:
            存在任一提及信息时返回结构化字典，否则返回 None。
        """
        mentions_data: Dict[str, Any] = {}

        if message.mentions:
            users = []
            for user in message.mentions:
                users.append({
                    "user_id": str(user.id),
                    "username": user.name,
                    "display_name": user.display_name,
                    "global_name": getattr(user, "global_name", None),
                    "server_nickname": getattr(user, "nick", None),
                    "is_bot": user.bot,
                    "discriminator": getattr(user, "discriminator", None),
                })
            mentions_data["users"] = users

        if message.role_mentions:
            roles = []
            for role in message.role_mentions:
                roles.append({
                    "role_id": str(role.id),
                    "role_name": role.name,
                    "color": str(role.color),
                    "mentionable": role.mentionable,
                })
            mentions_data["roles"] = roles

        if hasattr(message, "channel_mentions") and message.channel_mentions:
            channels = []
            for channel in message.channel_mentions:
                channels.append({
                    "channel_id": str(channel.id),
                    "channel_name": channel.name,
                    "channel_type": str(channel.type),
                })
            mentions_data["channels"] = channels

        if "@everyone" in message.content or "@here" in message.content:
            mentions_data["everyone"] = "@everyone" in message.content
            mentions_data["here"] = "@here" in message.content

        return mentions_data if mentions_data else None

    def _process_text_with_emojis(
        self, text: str, message: Optional[discord.Message] = None
    ) -> List[Seg]:
        """将原始文本中的 Discord 提及占位符替换为可读形式，再拆分为文本/表情分段。

        Args:
            text: 消息正文字符串。
            message: 若提供，则用于解析用户/角色/频道提及以替换 `<@...>` 等。

        Returns:
            文本与表情拆分后的 `Seg` 列表。
        """
        processed_text = text
        if message and message.mentions:
            for user in message.mentions:
                for pattern in (f"<@!{user.id}>", f"<@{user.id}>"):
                    if pattern in processed_text:
                        server_nick = getattr(user, "nick", None)
                        global_name = getattr(user, "global_name", None)
                        display = server_nick or global_name or user.display_name
                        processed_text = processed_text.replace(pattern, f"@{display}")

        if message and message.role_mentions:
            for role in message.role_mentions:
                role_pattern = f"<@&{role.id}>"
                if role_pattern in processed_text:
                    processed_text = processed_text.replace(role_pattern, f"@{role.name}")

        if message and hasattr(message, "channel_mentions"):
            for channel in message.channel_mentions:
                channel_pattern = f"<#{channel.id}>"
                if channel_pattern in processed_text:
                    processed_text = processed_text.replace(channel_pattern, f"#{channel.name}")

        return self._process_emoji_text(processed_text)

    def _process_emoji_text(self, text: str) -> List[Seg]:
        """按 Discord 自定义表情语法与 Unicode 表情规则将正文拆成多个 `Seg`（以文本为主）。

        Args:
            text: 已处理提及占位符后的正文。

        Returns:
            按自定义表情切分后的 `Seg` 列表；无表情时为单段文本。
        """
        unicode_emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"
            "\U0001F300-\U0001F5FF"
            "\U0001F680-\U0001F6FF"
            "\U0001F1E0-\U0001F1FF"
            "\U00002702-\U000027B0"
            "\U000024C2-\U0001F251"
            "]+",
            flags=re.UNICODE,
        )
        discord_custom_emoji_pattern = re.compile(r"<a?:(\w+):(\d+)>")

        has_unicode = bool(unicode_emoji_pattern.search(text))
        has_custom = bool(discord_custom_emoji_pattern.search(text))

        if not has_unicode and not has_custom:
            return [Seg(type="text", data=text)]

        segments: List[Seg] = []
        current_pos = 0

        for match in discord_custom_emoji_pattern.finditer(text):
            if match.start() > current_pos:
                before = text[current_pos : match.start()]
                if before.strip():
                    segments.append(Seg(type="text", data=before))

            emoji_name = match.group(1)
            is_animated = text[match.start() : match.end()].startswith("<a:")
            emoji_text = f"[动画:{emoji_name}]" if is_animated else f"[{emoji_name}]"
            segments.append(Seg(type="text", data=emoji_text))
            current_pos = match.end()

        if current_pos < len(text):
            remaining = text[current_pos:]
            if remaining.strip():
                segments.append(Seg(type="text", data=remaining))

        if has_unicode and not has_custom and not segments:
            segments.append(Seg(type="text", data=text))

        return segments if segments else [Seg(type="text", data=text)]

    async def _get_reply_context(self, message: discord.Message) -> Optional[str]:
        """拉取被引用消息的摘要字符串，用于插入回复链上下文（作者、截断正文与附件提示）。

        Args:
            message: 带 `reference` 的 `Message`。

        Returns:
            可读的引用前缀字符串；无引用时返回 None；拉取失败时返回带消息 ID 的占位说明。
        """
        try:
            if not message.reference or not message.reference.message_id:
                return None

            referenced_message = None
            try:
                cached = getattr(message.reference, "cached_message", None)
                if cached:
                    referenced_message = cached
                else:
                    referenced_message = await message.channel.fetch_message(
                        message.reference.message_id
                    )
            except (discord.NotFound, discord.Forbidden):
                return f"[回复消息{message.reference.message_id}]，说："
            except (discord.HTTPException, AttributeError):
                return f"[回复消息{message.reference.message_id}]，说："

            if not referenced_message:
                return f"[回复消息{message.reference.message_id}]，说："

            author_name = referenced_message.author.display_name
            author_id = referenced_message.author.id
            is_bot = referenced_message.author.bot
            content = referenced_message.content or "[无文本内容]"
            if len(content) > 100:
                content = content[:100] + "..."
            if referenced_message.attachments:
                content += f"[包含{len(referenced_message.attachments)}个附件]"

            user_type = "机器人" if is_bot else "用户"
            return f"[回复<{user_type}{author_name}:{author_id}>：{content}]，说："

        except Exception as exc:
            self._logger.error(f"处理回复上下文时发生错误: {exc}")
            return f"[回复消息{message.reference.message_id}]，说："

    async def _convert_reaction_to_maim(
        self,
        event_type: str,
        payload: discord.RawReactionActionEvent,
        client: discord.Client,
    ) -> Optional[MessageBase]:
        """将 Reaction 事件装配为含文本描述与 `reaction_event` 分段的 `MessageBase`。

        Args:
            event_type: reaction_add 或 reaction_remove 等。
            payload: 原始 Reaction 事件载荷。
            client: Bot 客户端，用于解析用户与频道/线程信息。

        Returns:
            成功返回 `MessageBase`；无法解析用户或异常时返回 None。
        """
        try:
            user, member = await self._resolve_reaction_user(payload, client)
            if not user:
                self._logger.error(f"无法获取用户 {payload.user_id} 的信息")
                return None

            user_display_name = getattr(user, "display_name", None) or user.name
            server_nickname = getattr(member, "nick", None) if member else None

            user_info = UserInfo(
                platform=self._platform_name,
                user_id=str(user.id),
                user_nickname=user_display_name,
                user_cardname=server_nickname,
            )

            group_info = None
            is_thread = False
            thread_name = None

            if payload.guild_id:
                group_info, is_thread, thread_name = await self._build_reaction_group_info(
                    payload, client
                )

            emoji = payload.emoji
            if emoji.is_unicode_emoji():
                emoji_str = emoji.name
                emoji_name = None
            else:
                emoji_str = f"<:{emoji.name}:{emoji.id}>"
                emoji_name = emoji.name

            emoji_meaning, emoji_display = get_emoji_meaning(emoji_str, emoji_name or "")
            action_text = "添加了" if event_type == "reaction_add" else "移除了"
            description = format_reaction_for_ai(emoji_str, emoji_name or "", 1, user_display_name)
            description = description.replace("添加了", action_text)

            message_segments: List[Seg] = []
            message_segments.append(Seg(type="text", data=description))

            reaction_metadata = {
                "event_type": event_type,
                "action": "add" if event_type == "reaction_add" else "remove",
                "user_id": str(payload.user_id),
                "user_name": user_display_name,
                "message_id": str(payload.message_id),
                "channel_id": str(payload.channel_id),
                "guild_id": str(payload.guild_id) if payload.guild_id else None,
                "emoji": emoji_str,
                "emoji_name": emoji_name,
                "emoji_display": emoji_display,
                "emoji_meaning": emoji_meaning,
                "is_thread": is_thread,
                "thread_name": thread_name if is_thread else None,
            }
            message_segments.append(Seg(type="reaction_event", data=reaction_metadata))

            format_info = FormatInfo(
                content_format=["text", "reaction_event"],
                accept_format=["text", "image", "emoji", "reply", "voice", "command", "file", "video", "reaction"],
            )

            timestamp = int(time.time() * 1000)
            unique_id = f"reaction_{payload.message_id}_{payload.user_id}_{event_type}_{timestamp}"

            message_info = BaseMessageInfo(
                platform=self._platform_name,
                message_id=unique_id,
                time=time.time(),
                user_info=user_info,
                group_info=group_info,
                format_info=format_info,
            )

            if len(message_segments) == 1:
                message_segment = message_segments[0]
            else:
                message_segment = Seg(type="seglist", data=message_segments)

            return MessageBase(
                message_info=message_info,
                message_segment=message_segment,
                raw_message=description,
            )

        except Exception as exc:
            self._logger.error(f"转换 reaction 事件时发生错误: {exc}")
            self._logger.debug(traceback.format_exc())
            return None

    async def _resolve_reaction_user(
        self, payload: discord.RawReactionActionEvent, client: discord.Client
    ) -> tuple[Optional[discord.User], Optional[discord.Member]]:
        """从载荷与缓存中解析操作 Reaction 的用户及公会内 `Member`（若存在）。

        Args:
            payload: 原始 Reaction 事件。
            client: 用于 `get_guild` / `fetch_member` / `fetch_user` 的客户端。

        Returns:
            `(User 或 None, Member 或 None)`；公会场景下优先填充 Member。
        """
        user: Optional[discord.User] = None
        member: Optional[discord.Member] = None

        if payload.member:
            member = payload.member
            user = payload.member
        else:
            guild = client.get_guild(payload.guild_id) if payload.guild_id else None
            if guild:
                member = guild.get_member(payload.user_id)
                if not member:
                    try:
                        member = await guild.fetch_member(payload.user_id)
                    except (discord.NotFound, discord.HTTPException):
                        pass
                if member:
                    user = member

            if not user:
                user = client.get_user(payload.user_id)
                if not user:
                    try:
                        user = await client.fetch_user(payload.user_id)
                    except (discord.NotFound, discord.HTTPException):
                        pass

        return user, member

    async def _build_reaction_group_info(
        self, payload: discord.RawReactionActionEvent, client: discord.Client
    ) -> tuple[Optional[GroupInfo], bool, Optional[str]]:
        """根据频道 ID 解析 `GroupInfo`，并判断是否在子区及子区名称。

        Args:
            payload: 含 `channel_id` / `guild_id` 的 Reaction 载荷。
            client: 用于 `get_channel` / `fetch_channel` / `get_guild` 的客户端。

        Returns:
            `(GroupInfo, 是否为子区, 子区名称或 None)`；频道不可见时仍返回基于 ID 构造的 `GroupInfo`。
        """
        is_thread = False
        thread_name = None

        channel = client.get_channel(payload.channel_id)
        if not channel:
            try:
                channel = await client.fetch_channel(payload.channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                channel = None

        guild = client.get_guild(payload.guild_id) if payload.guild_id else None
        guild_name = guild.name if guild else None

        if channel and isinstance(channel, discord.Thread):
            is_thread = True
            thread_name = channel.name
            parent_channel = getattr(channel, "parent", None)
            inherit = getattr(self._chat_config, "inherit_channel_memory", True)

            if parent_channel and inherit:
                channel_name = parent_channel.name
                group_id = str(parent_channel.id)
                group_name = f"[当前子区: {thread_name}] {channel_name}"
                if guild_name:
                    group_name += f" @ {guild_name}"
            else:
                group_id = str(channel.id)
                group_name = thread_name or ""
                if parent_channel:
                    group_name += f" [{parent_channel.name}]"
                if guild_name:
                    group_name += f" @ {guild_name}"
        elif channel:
            channel_name = channel.name if hasattr(channel, "name") else f"频道{channel.id}"
            group_id = str(channel.id)
            group_name = channel_name
            if guild_name:
                group_name += f" @ {guild_name}"
        else:
            group_id = str(payload.channel_id)
            group_name = f"频道{payload.channel_id}"
            if guild_name:
                group_name += f" @ {guild_name}"

        group_info = GroupInfo(
            platform=self._platform_name,
            group_id=group_id,
            group_name=group_name,
        )
        return group_info, is_thread, thread_name
