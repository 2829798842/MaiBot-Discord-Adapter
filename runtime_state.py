"""Discord 消息网关运行时状态管理。"""

from typing import Any, Optional, Protocol


class _GatewayCapabilityProtocol(Protocol):
    """描述 Host 侧消息网关能力的最小协议：用于上报连接就绪与断开。"""

    async def update_state(
        self,
        gateway_name: str,
        *,
        ready: bool,
        platform: str = "",
        account_id: str = "",
        scope: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> bool: ...


class DiscordRuntimeStateManager:
    """Discord 消息网关路由状态上报器。

    在 Bot 上线或下线时调用 Host 的 ``update_state``，同步网关是否可路由消息。
    """

    def __init__(
        self,
        gateway_capability: _GatewayCapabilityProtocol,
        logger: Any,
        gateway_name: str,
    ) -> None:
        """保存网关能力与名称，并初始化本地“已连接/已上报账号”缓存。

        Args:
            gateway_capability: 实现 ``update_state`` 的 Host 网关能力对象。
            logger: 用于记录警告与信息的日志器。
            gateway_name: 本插件注册的网关名称，与路由一致。
        """
        self._gateway_capability = gateway_capability
        self._gateway_name = gateway_name
        self._logger = logger
        self._runtime_state_connected: bool = False
        self._reported_account_id: Optional[str] = None

    async def report_connected(self, account_id: str, bot_name: str = "") -> bool:
        """向 Host 上报 Discord 网关已就绪。

        Args:
            account_id: Discord Bot 的用户 ID。
            bot_name: Bot 的显示名称（仅用于元数据）。

        Returns:
            bool: Host 接受了状态更新则返回 True。
        """
        normalized_id = str(account_id).strip()
        if not normalized_id:
            return False

        if self._runtime_state_connected and self._reported_account_id == normalized_id:
            return True

        accepted = False
        try:
            metadata: dict[str, Any] = {}
            if bot_name:
                metadata["bot_name"] = bot_name
            accepted = await self._gateway_capability.update_state(
                gateway_name=self._gateway_name,
                ready=True,
                platform="discord",
                account_id=normalized_id,
                metadata=metadata or None,
            )
        except Exception as exc:
            self._logger.warning(f"Discord 消息网关上报连接就绪状态失败: {exc}")
            return False

        if not accepted:
            self._logger.warning("Discord 消息网关连接已建立，但 Host 未接受运行时状态更新")
            return False

        self._runtime_state_connected = True
        self._reported_account_id = normalized_id
        self._logger.info(
            f"Discord 消息网关已激活路由: platform=discord account_id={normalized_id}"
        )
        return True

    async def report_disconnected(self) -> None:
        """向 Host 上报连接已断开，撤销消息网关路由。"""
        if not self._runtime_state_connected:
            self._reported_account_id = None
            return

        try:
            await self._gateway_capability.update_state(
                gateway_name=self._gateway_name,
                ready=False,
                platform="discord",
            )
        except Exception as exc:
            self._logger.warning(f"Discord 消息网关上报断开状态失败: {exc}")
        finally:
            self._runtime_state_connected = False
            self._reported_account_id = None
