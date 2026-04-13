"""将 MaiBot Seg 结构转换为 Discord 可接受的文本与附件。"""

import base64
import binascii
import io
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import discord
from maim_message import Seg

_logger = logging.getLogger("discord_adapter.content_builder")

SegmentDict = Dict[str, Any]


class DiscordContentBuilder:
    """将 MaiBot 的 Seg 片段树递归解析为 Discord 可用的正文与附件列表。

    支持文本、提及、表情/图片（base64）、语音等类型；会跳过 thread_context 与嵌套
    seglist，并对无法处理的类型输出占位说明文本。
    """

    def __init__(self, logger: Any = None) -> None:
        """初始化内容构建器。

        Args:
            logger: 可选日志记录器；未传入时使用模块默认 logger。
        """

        self._logger = logger or _logger

    def build(self, message_segment: Seg) -> Tuple[Optional[str], List[discord.File]]:
        """从根 Seg 递归构建 Discord 消息正文与附件列表。

        Args:
            message_segment: MaiBot 消息根片段（可含 seglist 嵌套）。

        Returns:
            二元组：(正文字符串，若无有效文本则为 None)、`discord.File` 附件列表。
        """
        content_parts: List[str] = []
        files: List[discord.File] = []

        def process(seg: Seg) -> None:
            if not getattr(seg, "type", None):
                return

            if seg.type == "thread_context":
                return

            if seg.type == "seglist" and isinstance(seg.data, list):
                for sub in seg.data:
                    if isinstance(sub, Seg):
                        process(sub)
                return

            if seg.type == "text":
                if seg.data is not None:
                    content_parts.append(str(seg.data))
                return

            if seg.type == "mention":
                mention_text = self._render_mention(seg.data)
                if mention_text:
                    content_parts.append(mention_text)
                return

            if seg.type in {"emoji", "image"}:
                file_item: Optional[discord.File] = None
                if isinstance(seg.data, str):
                    file_item = self._decode_image_to_attachment(seg.type, seg.data)
                if file_item:
                    files.append(file_item)
                else:
                    display = "表情" if seg.type == "emoji" else "图片"
                    content_parts.append(f"[{display}处理失败]")
                return

            if seg.type == "voice":
                voice_file = self._decode_voice(seg.data)
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

        process(message_segment)
        content_text = "\n".join(part for part in content_parts if part)
        return (content_text if content_text else None, files)

    def _render_mention(self, mention_data: Any) -> str:
        """将提及载荷规范化为 Discord 可发送的 @ 文本。

        Args:
            mention_data: 用户/角色提及数据（dict 或可解析为 dict 的 JSON 字符串）。

        Returns:
            空格连接的提及片段；无法解析时返回空字符串。
        """
        normalized = self._normalize_dict_payload(mention_data)
        if normalized is None:
            return ""

        parts: List[str] = []

        for user in normalized.get("users") or []:
            user_id = user.get("user_id")
            display = user.get("display_name") or user.get("username") or "未知用户"
            parts.append(f"<@{user_id}>" if user_id else f"@{display}")

        for role in normalized.get("roles") or []:
            role_id = role.get("role_id")
            role_name = role.get("role_name") or "未知角色"
            parts.append(f"<@&{role_id}>" if role_id else f"@{role_name}")

        if normalized.get("everyone"):
            parts.append("@everyone")

        return " ".join(parts)

    def _decode_image_to_attachment(self, seg_type: str, data: str) -> Optional[discord.File]:
        """将 base64 图片或表情数据解码为带合适扩展名的 Discord 附件。

        Args:
            seg_type: 片段类型，应为 ``emoji`` 或 ``image``（影响文件名前缀）。
            data: base64 编码的字节串。

        Returns:
            成功时返回 `discord.File`；数据为空或解码失败时返回 None。
        """
        if not data:
            return None
        try:
            decoded = base64.b64decode(str(data))
        except (ValueError, TypeError, binascii.Error) as exc:
            self._logger.warning(f"解码 {seg_type} base64 数据失败：{exc}")
            return None
        suffix = self._detect_image_suffix(decoded)
        prefix = "emoji" if seg_type == "emoji" else "image"
        filename = f"{prefix}_{int(time.time())}.{suffix}"
        return discord.File(fp=io.BytesIO(decoded), filename=filename)

    def _decode_voice(self, data: Any) -> Optional[discord.File]:
        """将 base64 语音数据解码为 WAV 附件。

        Args:
            data: base64 编码的字符串；非字符串或空串不予处理。

        Returns:
            成功时返回 `discord.File`；否则返回 None。
        """
        if not isinstance(data, str) or not data:
            return None
        try:
            decoded = base64.b64decode(str(data))
        except (ValueError, TypeError, binascii.Error) as exc:
            self._logger.warning(f"解码语音失败：{exc}")
            return None
        filename = f"voice_{int(time.time())}.wav"
        return discord.File(fp=io.BytesIO(decoded), filename=filename)

    @staticmethod
    def _detect_image_suffix(image_bytes: bytes) -> str:
        """按魔数推断图片扩展名，无法识别时退回 ``bin``。

        Args:
            image_bytes: 图片文件头字节。

        Returns:
            小写扩展名；未识别时为 ``bin``。
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
        return "bin"

    @staticmethod
    def _normalize_dict_payload(payload: Any) -> Optional[SegmentDict]:
        """将 dict 或可解析为对象的 JSON 字符串规范化为字典。

        Args:
            payload: 字典、JSON 字符串或其它类型。

        Returns:
            字典；无法解析或顶层非对象时返回 None。
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
