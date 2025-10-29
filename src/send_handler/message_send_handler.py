"""模块名称：send_handler.message_send_handler
主要功能：将 MaiBot Seg 结构转换为 Discord 可接受的文本与附件。"""

from __future__ import annotations

import base64
import binascii
import io
import json
import time
from typing import Any, Dict, List, Optional, Tuple

import discord
from maim_message import Seg

from ..logger import logger


SegmentDict = Dict[str, Any]


class DiscordContentBuilder:
    """构建 Discord 所需消息内容。"""

    def build(self, message_segment: Seg) -> Tuple[Optional[str], List[discord.File]]:
        """解析消息段树生成文本与附件。

        Args:
            message_segment: MaiBot 消息段根节点。

        Returns:
            Tuple[Optional[str], List[discord.File]]: 拼装完成的文本以及附件列表。
        """

        content_parts: List[str] = []
        files: List[discord.File] = []

        def process(seg: Seg) -> None:
            if not getattr(seg, "type", None):
                logger.debug(f"跳过缺少类型的消息段：{seg}")
                return

            if seg.type == "thread_context":
                logger.debug("跳过 thread_context 段，不参与文本拼接")
                return

            if seg.type == "seglist" and isinstance(seg.data, list):
                sub_segments: List[Any] = seg.data
                for sub_segment in sub_segments:
                    if isinstance(sub_segment, Seg):
                        process(sub_segment)
                    else:
                        logger.debug(f"seglist 子项不是 Seg 实例，已跳过：{sub_segment}")
                return

            if seg.type == "text":
                if seg.data is not None:
                    content_parts.append(str(seg.data))
                return

            if seg.type == "mention":
                mention_text: str = self._render_mention(seg.data)
                if mention_text:
                    content_parts.append(mention_text)
                return

            if seg.type in {"emoji", "image"}:
                file_item: Optional[discord.File] = None
                if isinstance(seg.data, str):
                    file_item = self._decode_image_to_attachment(seg.type, seg.data)
                else:
                    logger.debug(f"{seg.type} 段的 data 类型非字符串，已跳过：{type(seg.data)}")
                if file_item:
                    files.append(file_item)
                else:
                    display_text: str = "表情" if seg.type == "emoji" else "图片"
                    content_parts.append(f"[{display_text}处理失败]")
                return

            if seg.type == "voice":
                voice_file: Optional[discord.File] = self._decode_voice(seg.data)
                if voice_file:
                    files.append(voice_file)
                else:
                    content_parts.append("[语音处理失败]")
                return

            if seg.type == "video":
                content_parts.append(f"[视频: {seg.data}]")
                return

            if seg.type == "file":
                content_parts.append(f"[文件: {seg.data}]")
                return

            if seg.type == "command":
                content_parts.append(f"[命令: {seg.data}]")
                return

            if seg.type == "notify":
                content_parts.append(f"[通知: {seg.data}]")
                return

            if seg.type == "reply":
                return

            logger.debug(f"暂不支持的消息段类型：{seg.type}")

        process(message_segment)

        content_text: str = "\n".join(part for part in content_parts if part)
        return (content_text if content_text else None, files)

    def _render_mention(self, mention_data: Any) -> str:
        """渲染提及段为 Discord 文本。

        Args:
            mention_data: 提及信息，可能是字典或 JSON 字符串。

        Returns:
            str: Discord 兼容的提及字符串，无法生成时为空串。
        """

        normalized: SegmentDict | None = self._normalize_dict_payload(mention_data)
        if normalized is None:
            return ""

        parts: List[str] = []

        users: List[dict] = normalized.get("users") or []
        for user in users:
            user_id = user.get("user_id")
            display = user.get("display_name") or user.get("username") or "未知用户"
            parts.append(f"<@{user_id}>" if user_id else f"@{display}")

        roles: List[dict] = normalized.get("roles") or []
        for role in roles:
            role_id = role.get("role_id")
            role_name = role.get("role_name") or "未知角色"
            parts.append(f"<@&{role_id}>" if role_id else f"@{role_name}")

        if normalized.get("everyone"):
            parts.append("@everyone")

        mention_text: str = " ".join(parts)
        if mention_text:
            logger.debug(f"渲染 mention 文本：{mention_text}")
        return mention_text

    def _decode_image_to_attachment(self, seg_type: str, data: str) -> Optional[discord.File]:
        """解析图像或表情段并构建附件。

        Args:
            seg_type: 消息段类型，取值为 "emoji" 或 "image"。
            data: Base64 编码后的图像数据。

        Returns:
            Optional[discord.File]: 解码成功时返回 Discord 附件对象，否则为 None。
        """

        if not data:
            return None

        try:
            decoded: bytes = base64.b64decode(str(data))
        except (ValueError, TypeError, binascii.Error) as exc:
            logger.warning(f"解码 {seg_type} base64 数据失败：{exc}")
            return None

        suffix: str = self._detect_image_suffix(decoded)
        prefix: str = "emoji" if seg_type == "emoji" else "image"
        filename: str = f"{prefix}_{int(time.time())}.{suffix}"

        return discord.File(fp=io.BytesIO(decoded), filename=filename)

    def _decode_voice(self, data: str | Any) -> Optional[discord.File]:
        """解析语音段并构建附件。

        Args:
            data: Base64 编码语音数据。

        Returns:
            Optional[discord.File]: 解码后的语音文件附件，失败时为 None。
        """

        if not isinstance(data, str) or not data:
            return None

        try:
            decoded: bytes = base64.b64decode(str(data))
        except (ValueError, TypeError, binascii.Error) as exc:
            logger.warning(f"解码语音失败：{exc}")
            return None

        filename: str = f"voice_{int(time.time())}.wav"
        return discord.File(fp=io.BytesIO(decoded), filename=filename)

    @staticmethod
    def _detect_image_suffix(image_bytes: bytes) -> str:
        """识别图像二进制的文件后缀。

        Args:
            image_bytes: 图像的原始二进制数据。

        Returns:
            str: 推断出的文件后缀，无法确定时返回 "bin"。
        """

        if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            return "png"
        if image_bytes.startswith(b"\xff\xd8\xff"):
            return "jpg"
        if image_bytes.startswith(b"GIF87a") or image_bytes.startswith(b"GIF89a"):
            return "gif"
        if len(image_bytes) >= 12 and image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
            return "webp"
        if image_bytes.startswith(b"BM"):
            return "bmp"
        if image_bytes.startswith(b"\x00\x00\x01\x00") or (
                image_bytes.startswith(b"\x00\x00\x02\x00")
                ):
            return "ico"
        return "bin"

    @staticmethod
    def _normalize_dict_payload(payload: Any) -> SegmentDict | None:
        """尝试将段数据转换为字典。

        Args:
            payload: 消息段携带的 data 数据。

        Returns:
            SegmentDict | None: 成功解析后的字典，否则为 None。
        """

        if isinstance(payload, dict):
            return payload
        if isinstance(payload, str):
            try:
                loaded = json.loads(payload)
            except json.JSONDecodeError:
                return None
            return loaded if isinstance(loaded, dict) else None
        return None
