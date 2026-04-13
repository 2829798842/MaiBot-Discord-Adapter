"""Discord 聊天过滤器。"""

from typing import Any, Optional

from .config import DiscordChatConfig


class DiscordChatFilter:
    """基于名单配置的聊天消息过滤器。

    按服务器、频道、子区与用户维度的白名单/黑名单判断是否应处理某条入站消息。
    """

    def __init__(self, logger: Any) -> None:
        """创建过滤器实例；初始无聊天配置，调用 ``configure`` 后生效。

        Args:
            logger: 日志记录器（预留，供后续扩展使用）。
        """
        self._logger = logger
        self._config: Optional[DiscordChatConfig] = None

    def configure(self, config: DiscordChatConfig) -> None:
        """注入或更新聊天名单与继承策略等配置。

        Args:
            config: ``DiscordChatConfig`` 实例。
        """
        self._config = config

    def is_allowed(
        self,
        user_id: str,
        guild_id: Optional[str] = None,
        channel_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        is_thread: bool = False,
    ) -> bool:
        """检查消息是否被允许通过过滤器。

        Args:
            user_id: 用户 ID 字符串。
            guild_id: 服务器 ID 字符串。
            channel_id: 频道 ID 字符串。
            thread_id: 子区 ID 字符串。
            is_thread: 是否为子区消息。

        Returns:
            bool: 允许则返回 True。
        """
        if self._config is None:
            return True

        cfg = self._config

        if is_thread and not cfg.allow_thread_interaction:
            return False

        if not self._check_list(user_id, cfg.user_list_type, cfg.user_list):
            return False

        if guild_id is not None:
            if not self._check_list(guild_id, cfg.guild_list_type, cfg.guild_list):
                return False

        if channel_id is not None:
            if not self._check_list(channel_id, cfg.channel_list_type, cfg.channel_list):
                return False

        if is_thread and thread_id is not None and not cfg.inherit_channel_permissions:
            if not self._check_list(thread_id, cfg.thread_list_type, cfg.thread_list):
                return False

        return True

    @staticmethod
    def _check_list(target_id: str, list_type: str, id_list: list[str]) -> bool:
        """在名单非空时按白名单或黑名单规则判断 ``target_id`` 是否通过。

        Args:
            target_id: 当前上下文的 ID（用户/服务器/频道/子区）。
            list_type: ``whitelist`` 或 ``blacklist``。
            id_list: 已规范化的 ID 列表。

        Returns:
            bool: 允许继续处理为 True；名单为空时恒为 True。
        """
        if not id_list:
            return True
        if list_type == "whitelist":
            return target_id in id_list
        if list_type == "blacklist":
            return target_id not in id_list
        return True
