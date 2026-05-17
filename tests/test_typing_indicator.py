from pathlib import Path
from types import ModuleType, SimpleNamespace
import asyncio
import inspect
import importlib
import importlib.util
import sys

from maim_message import BaseMessageInfo, FormatInfo, MessageBase, Seg, UserInfo

sys.modules.setdefault("numpy", ModuleType("numpy"))

SDK_ROOT = Path(__file__).resolve().parents[4] / "maibot-plugin-sdk"
if str(SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_ROOT))


def _load_plugin_class():
    plugin_module = _load_adapter_module("plugin")
    return plugin_module.DiscordAdapterPlugin


def _load_adapter_module(module_name: str):
    package_name = "maibot_discord_adapter_under_test"
    package_root = Path(__file__).resolve().parents[1]
    if package_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            package_name,
            package_root / "__init__.py",
            submodule_search_locations=[str(package_root)],
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("无法加载 Discord 适配器测试包")
        module = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = module
        spec.loader.exec_module(module)
    return importlib.import_module(f"{package_name}.{module_name}")


def test_extract_typing_channel_id_uses_dm_source_channel() -> None:
    plugin_cls = _load_plugin_class()

    channel_id = plugin_cls._extract_discord_typing_channel_id(
        {
            "message_info": {
                "additional_config": {
                    "platform_io_target_channel_id": "987654321",
                    "platform_io_target_user_id": "123456789",
                }
            },
            "raw_message": [{"type": "text", "data": "ping"}],
        }
    )

    assert channel_id == 987654321


def test_extract_typing_channel_id_prefers_source_channel_over_group_id() -> None:
    plugin_cls = _load_plugin_class()

    channel_id = plugin_cls._extract_discord_typing_channel_id(
        {
            "message_info": {
                "additional_config": {
                    "platform_io_target_channel_id": "222222222",
                    "platform_io_target_group_id": "111111111",
                },
                "group_info": {
                    "group_id": "111111111",
                    "group_name": "父频道 @ guild",
                },
            },
            "raw_message": [{"type": "text", "data": "thread ping"}],
        }
    )

    assert channel_id == 222222222


def test_outbound_target_resolution_failure_stops_source_typing() -> None:
    asyncio.run(_run_outbound_target_resolution_failure_stops_source_typing())


async def _run_outbound_target_resolution_failure_stops_source_typing() -> None:
    plugin_cls = _load_plugin_class()
    plugin = plugin_cls.__new__(plugin_cls)

    stopped_channel_ids: list[int] = []
    plugin._client_manager = SimpleNamespace(stop_typing_indicator=stopped_channel_ids.append)
    plugin._thread_routing = SimpleNamespace(resolve_target_channel=lambda message: _none_async())
    plugin._content_builder = SimpleNamespace()
    plugin._voice_manager = None

    message = MessageBase(
        message_info=BaseMessageInfo(
            platform="discord",
            message_id="out-1",
            time=1.0,
            user_info=UserInfo(platform="discord", user_id="123456789"),
            format_info=FormatInfo(content_format=["text"], accept_format=["text"]),
            additional_config={"platform_io_target_channel_id": "987654321"},
        ),
        message_segment=Seg(type="text", data="pong"),
        raw_message="pong",
    )

    result = await plugin._handle_outbound_message(message)

    assert result == {"success": False, "error": "无法解析目标频道"}
    assert stopped_channel_ids == [987654321]


def test_outbound_message_stops_typing_before_channel_send() -> None:
    asyncio.run(_run_outbound_message_stops_typing_before_channel_send())


async def _run_outbound_message_stops_typing_before_channel_send() -> None:
    plugin_cls = _load_plugin_class()
    plugin = plugin_cls.__new__(plugin_cls)
    target_channel = SimpleNamespace(id=222222222, name="target")
    events: list[tuple[str, int | str]] = []

    plugin._client_manager = SimpleNamespace(
        stop_typing_indicator=lambda channel_id: events.append(("stop", channel_id))
    )
    plugin._thread_routing = SimpleNamespace(
        resolve_target_channel=lambda message: _value_async(target_channel),
        get_reply_reference=lambda message, channel: _value_async(None),
    )
    plugin._content_builder = SimpleNamespace(build=lambda segment: ("pong", ()))
    plugin._voice_manager = None
    plugin._ctx = SimpleNamespace(logger=SimpleNamespace(debug=lambda *args, **kwargs: None))

    async def fake_send_with_length_check(channel, content, files, reference):
        events.append(("send", channel.id))
        return SimpleNamespace(id=333333333)

    plugin._send_with_length_check = fake_send_with_length_check

    message = MessageBase(
        message_info=BaseMessageInfo(
            platform="discord",
            message_id="out-2",
            time=1.0,
            user_info=UserInfo(platform="discord", user_id="123456789"),
            format_info=FormatInfo(content_format=["text"], accept_format=["text"]),
            additional_config={"platform_io_target_channel_id": "111111111"},
        ),
        message_segment=Seg(type="text", data="pong"),
        raw_message="pong",
    )

    result = await plugin._handle_outbound_message(message)

    assert result == {
        "success": True,
        "external_message_id": "333333333",
        "message_id": "333333333",
    }
    assert events[:3] == [
        ("stop", 111111111),
        ("stop", 222222222),
        ("send", 222222222),
    ]


def test_start_typing_replaces_stopping_task_for_same_channel() -> None:
    asyncio.run(_run_start_typing_replaces_stopping_task_for_same_channel())


def test_typing_worker_uses_one_shot_pulses_not_context_manager() -> None:
    asyncio.run(_run_typing_worker_uses_one_shot_pulses_not_context_manager())


async def _run_typing_worker_uses_one_shot_pulses_not_context_manager() -> None:
    discord_client_module = _load_adapter_module("src.recv_handler.discord_client")
    manager = discord_client_module.DiscordClientManager.__new__(
        discord_client_module.DiscordClientManager
    )
    manager._typing_indicator_delay_seconds = 0.0
    manager._typing_indicator_timeout_seconds = 0.0
    manager._typing_indicator_tasks = {}
    manager._typing_indicator_stop_events = {}
    manager._logger = SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
    )

    events: list[str] = []

    class FakeTyping:
        def __await__(self):
            async def run():
                events.append("awaited")

            return run().__await__()

        async def __aenter__(self):
            events.append("entered")

        async def __aexit__(self, exc_type, exc, traceback):
            events.append("exited")

    channel = SimpleNamespace(id=987654321, typing=lambda: FakeTyping())
    stop_event = asyncio.Event()

    task = asyncio.create_task(
        manager._typing_indicator_worker(channel, 987654321, stop_event)
    )
    await asyncio.sleep(0)
    stop_event.set()
    await asyncio.wait_for(task, timeout=1.0)

    assert events == ["awaited"]


async def _run_start_typing_replaces_stopping_task_for_same_channel() -> None:
    discord_client_module = _load_adapter_module("src.recv_handler.discord_client")
    manager = discord_client_module.DiscordClientManager.__new__(
        discord_client_module.DiscordClientManager
    )

    channel = SimpleNamespace(id=987654321, typing=lambda: None)
    old_stop_event = asyncio.Event()
    old_stop_event.set()
    old_task = asyncio.create_task(asyncio.sleep(60))
    manager._typing_indicator_enabled = True
    manager._typing_indicator_tasks = {987654321: old_task}
    manager._typing_indicator_stop_events = {987654321: old_stop_event}
    manager._logger = SimpleNamespace(debug=lambda *args, **kwargs: None)

    async def worker(channel, channel_id, stop_event):
        await asyncio.sleep(60)

    manager._typing_indicator_worker = worker

    try:
        result = manager.start_typing_indicator(channel)

        assert result == 987654321
        assert manager._typing_indicator_tasks[987654321] is not old_task
        assert not manager._typing_indicator_stop_events[987654321].is_set()
    finally:
        old_task.cancel()
        replacement_task = manager._typing_indicator_tasks.get(987654321)
        if replacement_task is not None:
            replacement_task.cancel()
        await asyncio.gather(
            old_task,
            *(task for task in [replacement_task] if task is not None),
            return_exceptions=True,
        )


def test_sdk_hooks_start_and_stop_typing() -> None:
    asyncio.run(_run_sdk_hooks_start_and_stop_typing())


async def _run_sdk_hooks_start_and_stop_typing() -> None:
    plugin_cls = _load_plugin_class()
    plugin = plugin_cls.__new__(plugin_cls)

    calls: list[tuple[str, str | int]] = []

    class FakeClientManager:
        async def start_typing_for_session(self, session_id: str):
            calls.append(("start", session_id))

        def remember_typing_target(self, session_id: str, channel_id: int):
            calls.append(("remember", session_id))
            calls.append(("remember_channel", channel_id))

        def stop_typing_for_session(self, session_id: str):
            calls.append(("stop_session", session_id))

        def stop_typing_indicator(self, channel_id: int):
            calls.append(("stop_channel", channel_id))

    plugin._client_manager = FakeClientManager()
    plugin._load_settings = lambda: SimpleNamespace(
        platform=SimpleNamespace(platform_name="discord")
    )

    await plugin.remember_discord_typing_target(
        message={
            "platform": "discord",
            "session_id": "session-1",
            "message_info": {
                "additional_config": {"platform_io_target_channel_id": "987654321"}
            },
        }
    )
    await plugin.stop_discord_typing_before_send(
        message={
            "session_id": "session-1",
            "message_info": {
                "additional_config": {"platform_io_target_channel_id": "987654321"}
            },
        }
    )
    await plugin.stop_discord_typing_after_replyer_response(session_id="session-1")
    await plugin.stop_discord_typing_after_send(
        message={
            "session_id": "session-1",
            "message_info": {
                "additional_config": {"platform_io_target_channel_id": "987654321"}
            },
        }
    )

    assert calls == [
        ("remember", "session-1"),
        ("remember_channel", 987654321),
        ("start", "session-1"),
        ("stop_session", "session-1"),
        ("stop_channel", 987654321),
        ("stop_session", "session-1"),
        ("stop_session", "session-1"),
        ("stop_channel", 987654321),
    ]


def test_plugin_uses_existing_sdk_hook_names_for_typing() -> None:
    plugin_cls = _load_plugin_class()

    assert not hasattr(plugin_cls, "start_discord_typing_on_planner_request")
    assert hasattr(plugin_cls, "remember_discord_typing_target")
    assert hasattr(plugin_cls, "stop_discord_typing_before_send")
    assert hasattr(plugin_cls, "stop_discord_typing_after_replyer_response")
    assert hasattr(plugin_cls, "stop_discord_typing_after_send")
    assert not hasattr(plugin_cls, "start_discord_typing_before_reply_generate")
    assert not hasattr(plugin_cls, "stop_discord_typing_after_reply_complete")
    assert plugin_cls.remember_discord_typing_target.__maibot_component_info__.mode == "blocking"
    assert plugin_cls.stop_discord_typing_before_send.__maibot_component_info__.mode == "blocking"


def test_inbound_discord_message_records_and_starts_typing() -> None:
    asyncio.run(_run_inbound_discord_message_records_and_starts_typing())


async def _run_inbound_discord_message_records_and_starts_typing() -> None:
    plugin_cls = _load_plugin_class()
    plugin = plugin_cls.__new__(plugin_cls)

    calls: list[tuple[str, str | int]] = []

    class FakeClientManager:
        def remember_typing_target(self, session_id: str, channel_id: int):
            calls.append(("remember", session_id))
            calls.append(("channel", channel_id))

        async def start_typing_for_session(self, session_id: str):
            calls.append(("start", session_id))

    plugin._client_manager = FakeClientManager()
    plugin._load_settings = lambda: SimpleNamespace(
        platform=SimpleNamespace(platform_name="discord")
    )

    result = await plugin.remember_discord_typing_target(
        message={
            "platform": "discord",
            "session_id": "session-2",
            "message_info": {
                "additional_config": {"platform_io_target_channel_id": "123456789"}
            },
        }
    )

    assert result == {"action": "continue"}
    assert calls == [
        ("remember", "session-2"),
        ("channel", 123456789),
        ("start", "session-2"),
    ]


def test_stop_typing_indicator_cancels_active_task() -> None:
    asyncio.run(_run_stop_typing_indicator_cancels_active_task())


async def _run_stop_typing_indicator_cancels_active_task() -> None:
    discord_client_module = _load_adapter_module("src.recv_handler.discord_client")
    manager = discord_client_module.DiscordClientManager.__new__(
        discord_client_module.DiscordClientManager
    )
    stop_event = asyncio.Event()
    task = asyncio.create_task(asyncio.sleep(60))
    manager._typing_indicator_tasks = {987654321: task}
    manager._typing_indicator_stop_events = {987654321: stop_event}

    manager.stop_typing_indicator(987654321)
    await asyncio.gather(task, return_exceptions=True)

    assert stop_event.is_set()
    assert task.cancelled()
    assert manager._typing_indicator_tasks == {}
    assert manager._typing_indicator_stop_events == {}


def test_stop_typing_for_session_forgets_consumed_target() -> None:
    asyncio.run(_run_stop_typing_for_session_forgets_consumed_target())


async def _run_stop_typing_for_session_forgets_consumed_target() -> None:
    discord_client_module = _load_adapter_module("src.recv_handler.discord_client")
    manager = discord_client_module.DiscordClientManager.__new__(
        discord_client_module.DiscordClientManager
    )
    channel = SimpleNamespace(id=987654321)
    manager.client = SimpleNamespace(
        get_channel=lambda channel_id: channel if channel_id == 987654321 else None
    )
    manager._typing_targets_by_session_id = {"session-1": 987654321}
    manager._typing_indicator_enabled = True
    manager._typing_indicator_tasks = {}
    manager._typing_indicator_stop_events = {}
    manager._logger = SimpleNamespace(debug=lambda *args, **kwargs: None)
    started_channel_ids: list[int] = []

    def start_typing_indicator(channel):
        started_channel_ids.append(channel.id)
        return channel.id

    manager.start_typing_indicator = start_typing_indicator
    manager.stop_typing_indicator = lambda channel_id: None

    assert await manager.start_typing_for_session("session-1") == 987654321
    manager.stop_typing_for_session("session-1")

    assert await manager.start_typing_for_session("session-1") is None
    assert started_channel_ids == [987654321]
    assert "session-1" not in manager._typing_targets_by_session_id


def test_typing_defaults_to_enabled_single_pulse_window() -> None:
    config_module = _load_adapter_module("config")
    discord_client_module = _load_adapter_module("src.recv_handler.discord_client")

    assert config_module.DiscordChatConfig().show_typing_indicator is True
    assert config_module.DiscordChatConfig().typing_indicator_timeout_sec == 5
    signature = inspect.signature(discord_client_module.DiscordClientManager.__init__)
    assert signature.parameters["typing_indicator_timeout_sec"].default == 5


async def _none_async():
    return None


async def _value_async(value):
    return value
