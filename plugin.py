import asyncio
import json
from pathlib import Path
import time
import tomllib
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import discord
from maim_message import BaseMessageInfo, MessageBase, Seg

from maibot_sdk import MaiBotPlugin, MessageGateway, PluginConfigBase

from maim_message import FormatInfo, GroupInfo, UserInfo

from .config import DiscordPluginSettings
from .constants import DISCORD_GATEWAY_NAME
from .filters import DiscordChatFilter
from .runtime_state import DiscordRuntimeStateManager
from .src.recv_handler.discord_client import DiscordClientManager
from .src.recv_handler.message_handler import DiscordMessageHandler
from .src.send_handler.message_send_handler import DiscordContentBuilder
from .src.send_handler.thread_send_handler import ThreadRoutingManager
from .src.voice.tts.gptsovits_tts import GPTSoVITSTTSProvider
from .src.voice.voice_manager import VoiceManager


class DiscordAdapterPlugin(MaiBotPlugin):
    """Discord 消息网关插件。

    负责注册双工消息网关、管理 discord.py 客户端生命周期，并将 Host 出站消息路由到
    Discord 频道或语音 TTS；同时处理 Reaction 命令与超长消息拆分发送。
    """

    config_model: ClassVar[type[PluginConfigBase] | None] = DiscordPluginSettings
    _GSV_SCHEMA_AUTO_CHOICE: ClassVar[str] = "[auto]"
    _GSV_SCHEMA_CACHE_TTL_SECONDS: ClassVar[float] = 30.0

    def __init__(self) -> None:
        """初始化插件实例，各子组件在连接建立前保持为未绑定状态。"""
        super().__init__()
        self._client_manager: Optional[DiscordClientManager] = None
        self._runtime_state: Optional[DiscordRuntimeStateManager] = None
        self._thread_routing: Optional[ThreadRoutingManager] = None
        self._message_handler: Optional[DiscordMessageHandler] = None
        self._content_builder: Optional[DiscordContentBuilder] = None
        self._chat_filter: Optional[DiscordChatFilter] = None
        self._voice_manager: Optional[VoiceManager] = None
        self._client_task: Optional[asyncio.Task[None]] = None
        self._gsv_template_catalog_cache: Dict[
            str,
            tuple[float, Dict[str, Dict[str, List[str]]]],
        ] = {}

    async def on_load(self) -> None:
        """插件加载时根据当前配置启动或保持 Discord 连接。"""
        await self._restart_if_needed()

    async def on_unload(self) -> None:
        """插件卸载时停止客户端任务并释放相关资源。"""
        await self._stop_connection()

    async def on_config_update(self, scope: str, config_data: Dict[str, Any], version: str) -> None:
        """响应 Host 下发的配置变更，在作用于本插件时应用配置并视需要重连。

        Args:
            scope: 配置作用域；仅 ``"self"`` 时会应用 ``config_data``。
            config_data: 新的插件配置字典。
            version: 配置版本标识，可用于日志。
        """
        if scope != "self":
            return
        self.set_plugin_config(config_data)
        if version:
            self.ctx.logger.debug(f"Discord 适配器收到配置更新通知: {version}")
        await self._restart_if_needed()


    def normalize_plugin_config(self, config_data: Mapping[str, Any] | None) -> tuple[dict[str, Any], bool]:
        normalized_config, changed = super().normalize_plugin_config(config_data)
        voice_config = normalized_config.get("voice")
        gptsovits_config = normalized_config.get("gptsovits_tts")
        if not isinstance(voice_config, dict) or not isinstance(gptsovits_config, dict):
            return normalized_config, changed

        cleared_auto_choice = False
        for field_name in ("model", "voice"):
            raw_value = gptsovits_config.get(field_name)
            if isinstance(raw_value, str) and raw_value.strip() == self._GSV_SCHEMA_AUTO_CHOICE:
                gptsovits_config[field_name] = ""
                cleared_auto_choice = True

        raw_template_model = str(gptsovits_config.get("model") or "").strip()
        normalized_template_model = GPTSoVITSTTSProvider._normalize_template_model_name(
            raw_template_model
        )
        cleared_openai_model_id = False
        if raw_template_model and not normalized_template_model and raw_template_model.startswith("GSVI-"):
            gptsovits_config["model"] = ""
            cleared_openai_model_id = True

        normalized_catalog_values = self._normalize_gptsovits_catalog_values(
            voice_config=voice_config,
            gptsovits_config=gptsovits_config,
        )
        return (
            normalized_config,
            changed or cleared_auto_choice or cleared_openai_model_id or normalized_catalog_values,
        )

    def get_webui_config_schema(
        self,
        *,
        plugin_id: str = "",
        plugin_name: str = "",
        plugin_version: str = "",
        plugin_description: str = "",
        plugin_author: str = "",
    ) -> Dict[str, Any]:
        schema = super().get_webui_config_schema(
            plugin_id=plugin_id,
            plugin_name=plugin_name,
            plugin_version=plugin_version,
            plugin_description=plugin_description,
            plugin_author=plugin_author,
        )
        try:
            self._inject_gptsovits_template_choices(schema)
        except Exception as exc:
            self._get_logger().warning(
                f"Failed to enrich GPT-SoVITS config schema with local template choices: {exc}"
            )
        return schema

    def _inject_gptsovits_template_choices(self, schema: Dict[str, Any]) -> None:
        sections = schema.get("sections")
        if not isinstance(sections, dict):
            return

        gptsovits_section = sections.get("gptsovits_tts")
        if not isinstance(gptsovits_section, dict):
            return

        fields = gptsovits_section.get("fields")
        if not isinstance(fields, dict):
            return

        model_field = fields.get("model")
        voice_field = fields.get("voice")
        if not isinstance(model_field, dict) or not isinstance(voice_field, dict):
            return

        config_snapshot = self._load_schema_config_snapshot()
        voice_config = config_snapshot.get("voice")
        gptsovits_config = config_snapshot.get("gptsovits_tts")
        if not isinstance(voice_config, dict) or not isinstance(gptsovits_config, dict):
            return

        if str(voice_config.get("tts_provider") or "").strip() != "gptsovits":
            return

        api_base = str(gptsovits_config.get("api_base") or "http://127.0.0.1:8000").strip().rstrip("/")
        version = str(gptsovits_config.get("version") or "v4").strip() or "v4"
        self._inject_gptsovits_infer_single_choices(
            model_field=model_field,
            voice_field=voice_field,
            gptsovits_config=gptsovits_config,
            api_base=api_base,
            version=version,
        )

    def _inject_gptsovits_infer_single_choices(
        self,
        *,
        model_field: Dict[str, Any],
        voice_field: Dict[str, Any],
        gptsovits_config: Dict[str, Any],
        api_base: str,
        version: str,
    ) -> None:
        catalog, error_message = self._fetch_gptsovits_template_catalog(api_base, version)
        if not catalog:
            fallback_message = "当前未能从本地 GSV 拉取模板列表，将继续手动填写。"
            model_field["hint"] = self._append_schema_hint(model_field.get("hint"), fallback_message)
            voice_field["hint"] = self._append_schema_hint(voice_field.get("hint"), fallback_message)
            if error_message:
                self._get_logger().warning(
                    "Failed to fetch GPT-SoVITS template catalog for schema rendering "
                    f"[api={api_base}, version={version}, error={error_message}]"
                )
            return

        configured_model = GPTSoVITSTTSProvider._normalize_template_model_name(
            gptsovits_config.get("model")
        )
        model_choices = [self._GSV_SCHEMA_AUTO_CHOICE, *catalog.keys()]
        if configured_model and configured_model not in model_choices:
            model_choices.append(configured_model)

        model_field["ui_type"] = "select"
        model_field["choices"] = model_choices
        model_field["hint"] = self._append_schema_hint(
            model_field.get("hint"),
            f"已从本地 GSV 拉取 {len(catalog)} 个模板模型；选择“{self._GSV_SCHEMA_AUTO_CHOICE}”时会按运行时规则自动挑选。",
        )
        if configured_model and configured_model not in catalog:
            model_field["hint"] = self._append_schema_hint(
                model_field.get("hint"),
                "当前已保存的模板模型不在本地返回列表中，请重新选择或改回自动。",
            )

        configured_voice = str(gptsovits_config.get("voice") or "").strip()
        emotion_choices, language_summary = self._collect_gptsovits_emotion_choices(
            catalog=catalog,
            configured_model=configured_model,
            preferred_language=gptsovits_config.get("text_lang"),
        )
        if configured_voice and configured_voice not in emotion_choices:
            emotion_choices.append(configured_voice)

        if emotion_choices:
            voice_field["ui_type"] = "select"
            voice_field["choices"] = [self._GSV_SCHEMA_AUTO_CHOICE, *emotion_choices]
            voice_field["hint"] = self._append_schema_hint(
                voice_field.get("hint"),
                (
                    f"已从本地 GSV 拉取可选情感/音色 {len(emotion_choices)} 项"
                    f"{f'（{language_summary}）' if language_summary else ''}；"
                    f"选择“{self._GSV_SCHEMA_AUTO_CHOICE}”时会在运行时自动挑选。"
                ),
            )
        else:
            voice_field["hint"] = self._append_schema_hint(
                voice_field.get("hint"),
                "当前没有拉取到可选情感/音色；可以继续手动填写，或先保存模板模型后刷新配置页。",
            )

    def _load_schema_config_snapshot(self) -> Dict[str, Any]:
        config_path = Path(__file__).resolve().parent / "config.toml"
        raw_config: Mapping[str, Any] | None = None
        if config_path.exists():
            try:
                with config_path.open("rb") as file_obj:
                    loaded = tomllib.load(file_obj)
                if isinstance(loaded, dict):
                    raw_config = loaded
            except Exception as exc:
                self._get_logger().debug(
                    f"Failed to read plugin config snapshot for schema enrichment: {exc}"
                )

        if raw_config is None:
            current_config = self.get_plugin_config_data()
            raw_config = current_config if current_config else self.get_default_config()

        normalized_config, _ = self.normalize_plugin_config(raw_config)
        return normalized_config

    def _normalize_gptsovits_catalog_values(
        self,
        *,
        voice_config: Dict[str, Any],
        gptsovits_config: Dict[str, Any],
    ) -> bool:
        if str(voice_config.get("tts_provider") or "").strip() != "gptsovits":
            return False

        api_base = str(gptsovits_config.get("api_base") or "http://127.0.0.1:8000").strip().rstrip("/")
        version = str(gptsovits_config.get("version") or "v4").strip() or "v4"
        changed = False

        template_catalog, _ = self._fetch_gptsovits_template_catalog(api_base, version)
        if not template_catalog:
            return changed

        current_model_value = str(gptsovits_config.get("model") or "").strip()
        normalized_model_value = GPTSoVITSTTSProvider._normalize_template_model_name(
            current_model_value
        )
        matched_model_value = self._match_gptsovits_template_model(
            template_catalog,
            normalized_model_value,
        )
        if matched_model_value and matched_model_value != current_model_value:
            gptsovits_config["model"] = matched_model_value
            changed = True
            self._get_logger().debug(
                "Normalized GPT-SoVITS template model from local catalog "
                f"[from={current_model_value}, to={matched_model_value}]"
            )

        current_voice_value = str(gptsovits_config.get("voice") or "").strip()
        if current_voice_value:
            emotion_choices, _ = self._collect_gptsovits_emotion_choices(
                catalog=template_catalog,
                configured_model=matched_model_value or normalized_model_value,
                preferred_language=gptsovits_config.get("text_lang"),
            )
            matched_voice_value = self._match_gptsovits_choice(
                emotion_choices,
                current_voice_value,
            )
            if matched_voice_value and matched_voice_value != current_voice_value:
                gptsovits_config["voice"] = matched_voice_value
                changed = True
                self._get_logger().debug(
                    "Normalized GPT-SoVITS template emotion from local catalog "
                    f"[from={current_voice_value}, to={matched_voice_value}]"
                )

        return changed

    def _fetch_gptsovits_template_catalog(
        self,
        api_base: str,
        version: str,
    ) -> tuple[Optional[Dict[str, Dict[str, List[str]]]], str]:
        normalized_api_base = str(api_base or "").strip().rstrip("/")
        normalized_version = str(version or "v4").strip() or "v4"
        if not normalized_api_base:
            return None, "empty api_base"

        cache_key = f"{normalized_api_base}|{normalized_version}"
        cached = self._gsv_template_catalog_cache.get(cache_key)
        now = time.monotonic()
        if cached and now - cached[0] <= self._GSV_SCHEMA_CACHE_TTL_SECONDS:
            return cached[1], ""

        request_url = f"{normalized_api_base}/models/{quote(normalized_version, safe='')}"
        try:
            request = Request(request_url, headers={"Accept": "application/json"})
            with urlopen(request, timeout=3.0) as response:
                payload = json.loads(response.read().decode("utf-8", errors="ignore"))
        except HTTPError as exc:
            return None, f"HTTP {exc.code}"
        except URLError as exc:
            return None, str(exc.reason or exc)
        except Exception as exc:
            return None, str(exc)

        models = payload.get("models")
        if not isinstance(models, dict):
            return None, "response missing models"

        catalog: Dict[str, Dict[str, List[str]]] = {}
        for raw_model_name, raw_languages in models.items():
            model_name = str(raw_model_name or "").strip()
            if not model_name or not isinstance(raw_languages, dict):
                continue

            normalized_languages: Dict[str, List[str]] = {}
            for raw_language, raw_emotions in raw_languages.items():
                language = str(raw_language or "").strip()
                if not language or not isinstance(raw_emotions, list):
                    continue
                normalized_emotions = [
                    str(item).strip() for item in raw_emotions if str(item).strip()
                ]
                if normalized_emotions:
                    normalized_languages[language] = normalized_emotions

            if normalized_languages:
                catalog[model_name] = normalized_languages

        if not catalog:
            return None, "empty template catalog"

        self._gsv_template_catalog_cache[cache_key] = (now, catalog)
        self._get_logger().debug(
            "Loaded GPT-SoVITS template catalog for schema rendering "
            f"[api={normalized_api_base}, version={normalized_version}, models={len(catalog)}]"
        )
        return catalog, ""

    def _collect_gptsovits_emotion_choices(
        self,
        *,
        catalog: Dict[str, Dict[str, List[str]]],
        configured_model: str,
        preferred_language: Any,
    ) -> tuple[List[str], str]:
        matched_model_name = self._match_gptsovits_template_model(catalog, configured_model)
        preferred_language_name = GPTSoVITSTTSProvider._map_language_alias(preferred_language) or ""

        if matched_model_name:
            language_sets = [catalog.get(matched_model_name, {})]
            scope_label = f"模型 {matched_model_name}"
        else:
            language_sets = list(catalog.values())
            scope_label = "全部模板模型"

        preferred_emotions: List[str] = []
        fallback_emotions: List[str] = []
        seen: set[str] = set()

        for language_map in language_sets:
            if not isinstance(language_map, dict):
                continue
            for language_name, emotions in language_map.items():
                if not isinstance(emotions, list):
                    continue
                normalized_language = GPTSoVITSTTSProvider._map_language_alias(language_name) or str(
                    language_name
                ).strip()
                target_bucket = (
                    preferred_emotions
                    if preferred_language_name and normalized_language == preferred_language_name
                    else fallback_emotions
                )
                for raw_emotion in emotions:
                    emotion = str(raw_emotion or "").strip()
                    if not emotion or emotion in seen:
                        continue
                    seen.add(emotion)
                    target_bucket.append(emotion)

        if preferred_emotions:
            return preferred_emotions, f"{scope_label}，语言 {preferred_language_name}"
        if fallback_emotions:
            return fallback_emotions, scope_label
        return [], scope_label

    @staticmethod
    def _match_gptsovits_template_model(
        catalog: Dict[str, Dict[str, List[str]]],
        configured_model: str,
    ) -> str:
        model_name = str(configured_model or "").strip()
        if not model_name:
            return ""
        if model_name in catalog:
            return model_name
        fuzzy_matches = [
            candidate
            for candidate in catalog
            if model_name in candidate or candidate.endswith(model_name)
        ]
        return fuzzy_matches[0] if len(fuzzy_matches) == 1 else ""

    @staticmethod
    def _match_gptsovits_choice(choices: List[str], configured_value: str) -> str:
        normalized_value = str(configured_value or "").strip()
        if not normalized_value:
            return ""

        for candidate in choices:
            if normalized_value == candidate:
                return candidate

        lowercase_value = normalized_value.lower()
        for candidate in choices:
            if candidate.lower() == lowercase_value:
                return candidate

        fuzzy_matches = [
            candidate
            for candidate in choices
            if normalized_value in candidate
            or candidate.endswith(normalized_value)
            or candidate.lower().endswith(lowercase_value)
        ]
        if len(fuzzy_matches) == 1:
            return fuzzy_matches[0]

        if lowercase_value in {"default", "默认"}:
            for candidate in choices:
                if candidate in {"默认", "default", "Default"}:
                    return candidate

        return ""

    @staticmethod
    def _append_schema_hint(existing_hint: Any, extra_hint: str) -> str:
        base_hint = str(existing_hint or "").strip()
        extra = str(extra_hint or "").strip()
        if not base_hint:
            return extra
        if not extra or extra in base_hint:
            return base_hint
        return f"{base_hint} {extra}"

    @MessageGateway(
        name=DISCORD_GATEWAY_NAME,
        route_type="duplex",
        platform="discord",
        protocol="discord",
        description="Discord Bot 双工消息网关",
    )
    async def handle_discord_gateway(
        self,
        message: Dict[str, Any],
        route: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """处理 Host 经消息网关转发的出站数据，解析为 MaiBot 消息后发往 Discord。

        Args:
            message: 序列化后的消息体字典。
            route: 路由信息（当前未使用）。
            metadata: 附加元数据（当前未使用）。
            **kwargs: 兼容网关调用的额外参数（当前未使用）。

        Returns:
            dict: 至少包含 ``success``；失败时包含 ``error`` 说明原因。
        """
        del metadata, kwargs

        if self._client_manager is None or self._client_manager.client is None:
            return {"success": False, "error": "Discord 客户端未就绪"}

        try:
            maim_message = self._deserialize_outbound_message(message)
        except (ValueError, TypeError) as exc:
            return {"success": False, "error": f"消息解析失败: {exc}"}

        message_info = maim_message.message_info
        if not isinstance(message_info, BaseMessageInfo):
            return {"success": False, "error": "消息缺少有效的 message_info"}

        segment = maim_message.message_segment
        if not isinstance(segment, Seg) or not getattr(segment, "type", None):
            return {"success": False, "error": "消息缺少有效的消息段"}

        segment_type = getattr(segment, "type", None)

        if segment_type == "command":
            return await self._handle_outbound_command(maim_message)

        if segment_type == "notify":
            self.ctx.logger.debug(f"收到 notify 消息，已忽略：{segment.data}")
            return {"success": True}

        return await self._handle_outbound_message(maim_message)

    def _deserialize_outbound_message(self, message: Dict[str, Any]) -> MessageBase:
        """兼容解析 Host MessageDict 与旧版 maim_message 字典。"""
        if isinstance(message.get("message_segment"), dict):
            return MessageBase.from_dict(message)

        raw_message = message.get("raw_message")
        if isinstance(raw_message, list):
            return self._build_message_from_host_dict(message)

        raise ValueError("消息字典既不是 maim_message 格式，也不是 Host MessageDict 格式")

    def _build_message_from_host_dict(self, message: Dict[str, Any]) -> MessageBase:
        """将 Host 运行时的 `MessageDict` 转换为插件内部使用的 `MessageBase`。"""
        message_info_dict = message.get("message_info", {})
        if not isinstance(message_info_dict, dict):
            raise ValueError("消息缺少有效的 message_info")

        user_info_dict = message_info_dict.get("user_info", {})
        if not isinstance(user_info_dict, dict):
            raise ValueError("消息缺少有效的 message_info.user_info")

        platform = str(message.get("platform") or "discord").strip() or "discord"
        user_id = str(user_info_dict.get("user_id") or "").strip()
        user_nickname = str(user_info_dict.get("user_nickname") or user_id).strip() or user_id
        if not user_id:
            raise ValueError("消息缺少有效的 user_id")

        user_info = UserInfo(
            platform=platform,
            user_id=user_id,
            user_nickname=user_nickname,
            user_cardname=self._normalize_optional_string(user_info_dict.get("user_cardname")),
        )

        group_info = None
        group_info_dict = message_info_dict.get("group_info")
        if isinstance(group_info_dict, dict):
            group_id = str(group_info_dict.get("group_id") or "").strip()
            if group_id:
                group_info = GroupInfo(
                    platform=platform,
                    group_id=group_id,
                    group_name=str(group_info_dict.get("group_name") or group_id).strip() or group_id,
                )

        raw_message = message.get("raw_message", [])
        if not isinstance(raw_message, list):
            raise ValueError("消息缺少有效的 raw_message")

        segments = self._convert_host_raw_message_to_segments(raw_message)
        fallback_text = str(message.get("processed_plain_text") or message.get("display_message") or "").strip()
        if not segments and fallback_text:
            segments = [Seg(type="text", data=fallback_text)]
        if not segments:
            raise ValueError("消息缺少可发送的内容")

        format_info = FormatInfo(
            content_format=self._infer_content_formats(segments),
            accept_format=["text", "image", "emoji", "reply", "voice", "command", "file", "video"],
        )

        timestamp_raw = message.get("timestamp")
        try:
            timestamp = float(timestamp_raw) if timestamp_raw is not None else time.time()
        except (TypeError, ValueError):
            timestamp = time.time()

        additional_config = message_info_dict.get("additional_config")
        message_info = BaseMessageInfo(
            platform=platform,
            message_id=str(message.get("message_id") or f"discord-out-{int(time.time() * 1000)}"),
            time=timestamp,
            user_info=user_info,
            group_info=group_info,
            format_info=format_info,
            additional_config=additional_config if isinstance(additional_config, dict) else None,
        )

        message_segment = segments[0] if len(segments) == 1 else Seg(type="seglist", data=segments)
        return MessageBase(
            message_info=message_info,
            message_segment=message_segment,
            raw_message=fallback_text,
        )

    def _convert_host_raw_message_to_segments(self, raw_message: List[Dict[str, Any]]) -> List[Seg]:
        """将 Host 标准片段列表转换为 Discord 发送链可消费的 `Seg` 列表。"""
        segments: List[Seg] = []

        for item in raw_message:
            if not isinstance(item, dict):
                continue

            item_type = str(item.get("type") or "").strip()
            item_data = item.get("data")

            if item_type == "text":
                text = str(item_data or "")
                if text:
                    segments.append(Seg(type="text", data=text))
                continue

            if item_type in {"image", "emoji", "voice"}:
                binary_data = item.get("binary_data_base64")
                if isinstance(binary_data, str) and binary_data:
                    segments.append(Seg(type=item_type, data=binary_data))
                    continue

                fallback_text = str(item_data or "").strip()
                placeholder_map = {
                    "image": "[图片]",
                    "emoji": "[表情]",
                    "voice": "[语音]",
                }
                segments.append(Seg(type="text", data=fallback_text or placeholder_map[item_type]))
                continue

            if item_type == "at":
                mention_data = item_data if isinstance(item_data, dict) else {}
                target_user_id = str(mention_data.get("target_user_id") or "").strip()
                target_user_nickname = self._normalize_optional_string(
                    mention_data.get("target_user_nickname")
                )
                target_user_cardname = self._normalize_optional_string(
                    mention_data.get("target_user_cardname")
                )
                if target_user_id:
                    segments.append(
                        Seg(
                            type="mention",
                            data={
                                "users": [
                                    {
                                        "user_id": target_user_id,
                                        "display_name": target_user_nickname or target_user_cardname or target_user_id,
                                        "server_nickname": target_user_cardname,
                                        "username": target_user_nickname or target_user_id,
                                    }
                                ]
                            },
                        )
                    )
                continue

            if item_type == "reply":
                target_message_id = ""
                if isinstance(item_data, dict):
                    target_message_id = str(item_data.get("target_message_id") or "").strip()
                elif item_data is not None:
                    target_message_id = str(item_data).strip()
                if target_message_id:
                    segments.append(Seg(type="reply", data={"message_id": target_message_id}))
                continue

            if item_type == "dict" and isinstance(item_data, dict):
                custom_type = str(item_data.get("type") or "").strip()
                if custom_type and custom_type not in {"seglist", "dict"}:
                    segments.append(Seg(type=custom_type, data=item_data.get("data")))
                continue

            if item_type == "forward":
                segments.append(Seg(type="text", data="[转发消息]"))

        return segments

    @staticmethod
    def _infer_content_formats(segments: List[Seg]) -> List[str]:
        """根据片段列表推导 `FormatInfo.content_format`。"""
        formats: List[str] = []
        for seg in segments:
            seg_type = str(getattr(seg, "type", "") or "").strip()
            if seg_type and seg_type not in formats:
                formats.append(seg_type)
        return formats or ["text"]

    @staticmethod
    def _normalize_optional_string(value: Any) -> Optional[str]:
        """将任意值规整为可选字符串。"""
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None


    async def _handle_outbound_message(self, message: MessageBase) -> Dict[str, Any]:
        """将普通文本/媒体出站消息解析目标频道并发送（含语音频道 TTS 分支）。

        Args:
            message: 已解析的 ``MessageBase`` 实例。

        Returns:
            dict: ``success`` 表示是否发送成功；失败时 ``error`` 为原因说明。
        """
        if self._thread_routing is None or self._content_builder is None:
            return {"success": False, "error": "出站组件未初始化"}

        additional_config = getattr(message.message_info, "additional_config", None)
        voice_output_requested = bool(
            isinstance(additional_config, dict)
            and additional_config.get("discord_voice_output")
        )

        target_channel = await self._thread_routing.resolve_target_channel(message)
        if target_channel is None:
            return {"success": False, "error": "无法解析目标频道"}

        target_channel_id = getattr(target_channel, "id", None)
        target_channel_name = getattr(target_channel, "name", "unknown")
        target_channel_type = type(target_channel).__name__
        self.ctx.logger.debug(
            "Discord outbound target resolved "
            f"[channel_id={target_channel_id}, channel={target_channel_name}, "
            f"type={target_channel_type}, voice_output_requested={voice_output_requested}, "
            f"voice_manager={'yes' if self._voice_manager else 'no'}]"
        )

        if isinstance(target_channel, (discord.VoiceChannel, discord.StageChannel)):
            if self._voice_manager is not None:
                self.ctx.logger.info(
                    "Discord outbound message targets a voice channel and will be routed through TTS "
                    f"[channel_id={target_channel.id}, channel={target_channel.name}, type={target_channel_type}]"
                )
                voice_result = await self._handle_voice_outbound(
                    message,
                    channel_id=target_channel.id,
                    text_channel=target_channel,
                )
                if voice_result.get("success"):
                    return voice_result

                if voice_result.get("text_sent"):
                    self.ctx.logger.error(
                        "Discord voice TTS delivery failed after text echo was already sent "
                        f"[channel_id={target_channel.id}, error={voice_result.get('error', '')}]"
                    )
                    return {
                        "success": True,
                        "warning": voice_result.get("error") or "TTS failed after text fallback",
                        "tts_success": False,
                    }

                self.ctx.logger.error(
                    "Discord voice TTS delivery failed; falling back to text chat delivery in the voice channel "
                    f"[channel_id={target_channel.id}, error={voice_result.get('error', '')}]"
                )
            else:
                self.ctx.logger.error(
                    "Discord outbound target is a voice channel, but voice manager is unavailable; "
                    f"falling back to text chat delivery [channel_id={target_channel.id}]"
                )

        content, files = self._content_builder.build(message.message_segment)
        reference = await self._thread_routing.get_reply_reference(message, target_channel)

        if not content and not files:
            return {"success": False, "error": "消息内容为空且无附件"}

        try:
            sent_message = await self._send_with_length_check(target_channel, content, files, reference)
            result: Dict[str, Any] = {"success": True}
            if sent_message is not None:
                result["external_message_id"] = str(sent_message.id)
                result["message_id"] = str(sent_message.id)
            return result
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    async def _handle_voice_outbound(
        self,
        message: MessageBase,
        *,
        channel_id: Optional[int] = None,
        text_channel: Optional[discord.abc.Messageable] = None,
    ) -> Dict[str, Any]:
        """TTS 播报分支：将出站文本合成语音并在语音频道播放。"""
        if self._voice_manager is None or self._content_builder is None:
            self.ctx.logger.error("Discord voice outbound aborted: voice components are not initialized")
            return {"success": False, "error": "语音组件未初始化", "text_sent": False}

        content, _ = self._content_builder.build(message.message_segment)
        if not content or not content.strip():
            self.ctx.logger.error("Discord voice outbound aborted: message content is empty after rendering")
            return {"success": False, "error": "语音出站消息无文本内容", "text_sent": False}

        settings = self._load_settings()
        provider = getattr(self._voice_manager, "tts_provider", None)
        provider_name = type(provider).__name__ if provider is not None else "None"
        target_channel_id = channel_id or self._voice_manager.get_connected_channel_id()
        self.ctx.logger.info(
            "Discord voice outbound starting "
            f"[channel_id={target_channel_id}, chars={len(content)}, provider={provider_name}, "
            f"text_echo={'on' if settings.voice.send_text_in_voice else 'off'}]"
        )
        text_sent = False
        if settings.voice.send_text_in_voice:
            target_text_channel = text_channel
            if target_text_channel is None:
                resolved_channel_id = channel_id or self._voice_manager.get_connected_channel_id()
                if resolved_channel_id and self._client_manager and self._client_manager.client:
                    target_text_channel = self._client_manager.client.get_channel(
                        resolved_channel_id
                    )
            if target_text_channel and hasattr(target_text_channel, "send"):
                self.ctx.logger.debug(
                    "Discord voice outbound will also send text to the voice-channel chat "
                    f"[channel_id={getattr(target_text_channel, 'id', None)}]"
                )
                try:
                    await target_text_channel.send(content=content)
                    text_sent = True
                except Exception as exc:
                    self.ctx.logger.warning(f"发送语音频道文字失败: {exc}")
            else:
                self.ctx.logger.warning(
                    "Discord voice outbound requested text echo, but no writable voice text channel was resolved"
                )

        self.ctx.logger.debug(
            "Discord voice outbound invoking voice manager speak "
            f"[channel_id={target_channel_id}, chars={len(content)}]"
        )
        success = await self._voice_manager.speak(content, channel_id=channel_id)
        if success:
            self.ctx.logger.info(
                "Discord voice outbound completed successfully "
                f"[channel_id={target_channel_id}, provider={provider_name}, text_echo_sent={text_sent}]"
            )
        else:
            provider_last_error = ""
            if provider is not None:
                getter = getattr(provider, "get_last_error", None)
                if callable(getter):
                    try:
                        provider_last_error = str(getter() or "").strip()
                    except Exception:
                        provider_last_error = ""
                elif hasattr(provider, "_last_error"):
                    provider_last_error = str(getattr(provider, "_last_error") or "").strip()
            error_message = (
                f"TTS 播报失败: {provider_last_error}"
                if provider_last_error
                else "TTS 播报失败"
            )
            self.ctx.logger.error(
                "Discord voice outbound failed "
                f"[channel_id={target_channel_id}, provider={provider_name}, text_echo_sent={text_sent}, "
                f"provider_error={provider_last_error or '<empty>'}]"
            )
        return {
            "success": success,
            "error": "" if success else error_message,
            "text_sent": text_sent,
        }

    async def _handle_outbound_command(self, message: MessageBase) -> Dict[str, Any]:
        """处理 ``command`` 类型消息段，按 ``data.type`` 分发到具体子处理逻辑。

        Args:
            message: 含 command 类型消息段的 ``MessageBase``。

        Returns:
            dict: 子处理器返回的结果字典；未知类型时 ``success`` 为 False。
        """
        segment = message.message_segment
        command_data = segment.data if hasattr(segment, "data") else {}
        command_type = command_data.get("type", "") if isinstance(command_data, dict) else ""

        if command_type == "reaction":
            return await self._handle_reaction_command(command_data)

        self.ctx.logger.warning(f"收到未知 command 类型: {command_type}")
        return {"success": False, "error": f"未知命令类型: {command_type}"}

    async def _handle_reaction_command(self, command_data: Dict[str, Any]) -> Dict[str, Any]:
        """在指定频道消息上添加或移除表情反应。

        Args:
            command_data: 需包含 ``message_id``、``channel_id``、``emoji``；可选 ``action``（``add``/``remove``）。

        Returns:
            dict: 操作是否成功及失败时的 ``error`` 信息。
        """
        if self._client_manager is None or self._client_manager.client is None:
            return {"success": False, "error": "Discord 客户端未就绪"}

        action = command_data.get("action", "add")
        target_message_id = command_data.get("message_id")
        channel_id = command_data.get("channel_id")
        emoji_str = command_data.get("emoji")

        if not target_message_id or not channel_id or not emoji_str:
            return {"success": False, "error": "缺少必要参数 (message_id, channel_id, emoji)"}

        client = self._client_manager.client
        try:
            channel = client.get_channel(int(channel_id))
            if not channel:
                channel = await client.fetch_channel(int(channel_id))

            target_msg = await channel.fetch_message(int(target_message_id))

            if action == "add":
                await target_msg.add_reaction(emoji_str)
            elif action == "remove":
                await target_msg.remove_reaction(emoji_str, client.user)
            else:
                return {"success": False, "error": f"未知 reaction 操作: {action}"}

            return {"success": True}

        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            return {"success": False, "error": str(exc)}
        except (ValueError, TypeError) as exc:
            return {"success": False, "error": f"参数格式错误: {exc}"}


    MAX_MESSAGE_LENGTH: int = 2000

    async def _send_with_length_check(
        self,
        channel: discord.abc.Messageable,
        content: Optional[str],
        files: Sequence[discord.File],
        reference: Optional[discord.Message],
    ) -> Optional[discord.Message]:
        """在 Discord 消息长度与附件数量限制下发送内容；超长文本会拆分发送。

        Args:
            channel: 可发送消息的 Discord 频道或线程等对象。
            content: 文本内容，可为空（仅发附件时）。
            files: 待上传的附件序列，超过上限时截断为前 10 个。
            reference: 可选的回复引用消息。
        """
        MAX_FILES = 10
        first_sent_message: Optional[discord.Message] = None
        if files:
            file_list = list(files)
            if len(file_list) > MAX_FILES:
                self.ctx.logger.warning(
                    f"消息包含 {len(file_list)} 个文件，超过限制，仅发送前 {MAX_FILES} 个"
                )
                file_list = file_list[:MAX_FILES]

            send_content: Optional[str] = None
            if content and len(content) <= self.MAX_MESSAGE_LENGTH:
                send_content = content
                content = None

            first_sent_message = await channel.send(
                content=send_content, files=file_list, reference=reference
            )

        if content:
            if len(content) <= self.MAX_MESSAGE_LENGTH:
                sent_message = await channel.send(
                    content=content, reference=reference if not files else None
                )
                if first_sent_message is None:
                    first_sent_message = sent_message
            else:
                sent_message = await self._send_long_message(
                    channel, content, reference if not files else None
                )
                if first_sent_message is None:
                    first_sent_message = sent_message

        return first_sent_message

    async def _send_long_message(
        self,
        channel: discord.abc.Messageable,
        content: str,
        reference: Optional[discord.Message],
    ) -> Optional[discord.Message]:
        """将超长纯文本拆成多条消息依次发送，首条可携带回复引用。

        Args:
            channel: 目标可发送对象。
            content: 完整长文本。
            reference: 仅第一条片段使用的回复引用。
        """
        max_len = self.MAX_MESSAGE_LENGTH - 10
        if "```" in content:
            parts = self._split_preserve_codeblocks(content, max_len)
        else:
            parts = self._split_by_lines(content, max_len)

        first_sent_message: Optional[discord.Message] = None
        for index, part in enumerate(parts):
            if part.strip():
                try:
                    sent_message = await channel.send(
                        content=part, reference=reference if index == 0 else None
                    )
                    if first_sent_message is None:
                        first_sent_message = sent_message
                except (discord.HTTPException, discord.Forbidden) as exc:
                    self.ctx.logger.error(f"发送消息片段失败：{exc}")

        return first_sent_message

    @staticmethod
    def _split_preserve_codeblocks(content: str, max_len: int) -> List[str]:
        """在不超过 ``max_len`` 的前提下按行累积，并尽量保持 Markdown 代码块边界完整。

        Args:
            content: 原始文本。
            max_len: 单段最大字符数。

        Returns:
            list[str]: 拆分后的文本段列表。
        """
        parts: List[str] = []
        current = ""
        in_codeblock = False
        codeblock_start = ""

        for line in content.split("\n"):
            if line.strip().startswith("```"):
                if not in_codeblock:
                    in_codeblock = True
                    codeblock_start = line.strip()
                else:
                    in_codeblock = False

            test = f"{current}\n{line}" if current else line

            if len(test) > max_len:
                if in_codeblock:
                    parts.append(current + "\n```")
                    current = codeblock_start + "\n" + line
                else:
                    if current:
                        parts.append(current)
                    current = line
            else:
                current = test

        if current:
            parts.append(current)
        return parts

    @staticmethod
    def _split_by_lines(content: str, max_len: int) -> List[str]:
        """按行合并为段，单行超长时在 ``max_len`` 处硬切分。

        Args:
            content: 原始文本。
            max_len: 单段最大字符数。

        Returns:
            list[str]: 拆分后的文本段列表。
        """
        parts: List[str] = []
        current = ""

        for line in content.split("\n"):
            if len(line) > max_len:
                if current:
                    parts.append(current)
                    current = ""
                while len(line) > max_len:
                    parts.append(line[:max_len])
                    line = line[max_len:]
                current = line
            else:
                test = f"{current}\n{line}" if current else line
                if len(test) > max_len:
                    parts.append(current)
                    current = line
                else:
                    current = test

        if current:
            parts.append(current)
        return parts


    def _load_settings(self) -> DiscordPluginSettings:
        """返回当前插件配置，类型断言为 ``DiscordPluginSettings``。

        Returns:
            DiscordPluginSettings: 当前 ``self.config`` 的配置模型实例。
        """
        return cast(DiscordPluginSettings, self.config)

    async def _restart_if_needed(self) -> None:
        """先停止现有连接，再在配置与运行环境允许时重建组件并启动 Discord 客户端任务。"""
        settings = self._load_settings()

        await self._stop_connection()

        if not settings.should_connect():
            self.ctx.logger.info("Discord 适配器保持空闲状态（插件未启用）")
            return

        if not settings.validate_runtime_config(self.ctx.logger):
            return

        token = settings.connection.token

        self._ensure_components(settings, token)

        client_manager = self._client_manager
        if client_manager is None:
            return
        self._client_task = asyncio.create_task(client_manager.start())
        client_manager.start_monitor()

        self.ctx.logger.info("Discord 适配器启动任务已创建")

    def _ensure_components(self, settings: DiscordPluginSettings, token: str) -> None:
        """初始化或更新聊天过滤、运行时状态、消息处理、路由、客户端及语音等子组件。

        Args:
            settings: 完整插件配置。
            token: Discord Bot Token，传入 ``DiscordClientManager``。
        """
        platform_name = settings.platform.platform_name

        if self._chat_filter is None:
            self._chat_filter = DiscordChatFilter(self.ctx.logger)
        self._chat_filter.configure(settings.chat)

        if self._runtime_state is None:
            self._runtime_state = DiscordRuntimeStateManager(
                gateway_capability=self.ctx.gateway,
                logger=self.ctx.logger,
                gateway_name=DISCORD_GATEWAY_NAME,
            )

        if self._message_handler is None:
            self._message_handler = DiscordMessageHandler(
                logger=self.ctx.logger,
                platform_name=platform_name,
                chat_config=settings.chat,
            )
        else:
            self._message_handler.update_config(platform_name, settings.chat)

        if self._content_builder is None:
            self._content_builder = DiscordContentBuilder(logger=self.ctx.logger)

        if self._thread_routing is None:
            self._thread_routing = ThreadRoutingManager(
                logger=self.ctx.logger,
                chat_config=settings.chat,
            )
        else:
            self._thread_routing.update_config(settings.chat)

        self._client_manager = DiscordClientManager(
            logger=self.ctx.logger,
            token=token,
            intents_config=settings.get_intents_dict(),
            gateway_name=DISCORD_GATEWAY_NAME,
            gateway_capability=self.ctx.gateway,
            message_handler=self._message_handler,
            thread_routing_manager=self._thread_routing,
            chat_filter=self._chat_filter,
            filter_config=settings.filters,
            connection_check_interval=settings.connection.connection_check_interval,
            retry_delay=settings.connection.retry_delay,
        )

        self._voice_manager = self._build_voice_manager(settings)

        runtime_state = self._runtime_state
        voice_manager = self._voice_manager

        async def _on_connected() -> None:
            if self._client_manager and self._client_manager.client and self._client_manager.client.user:
                bot_user = self._client_manager.client.user
                await runtime_state.report_connected(
                    account_id=str(bot_user.id),
                    bot_name=str(bot_user),
                )
                if voice_manager:
                    voice_manager.bot = self._client_manager.client
                    await voice_manager.start()

        async def _on_disconnected() -> None:
            await runtime_state.report_disconnected()
            if voice_manager:
                await voice_manager.stop()

        self._client_manager.set_lifecycle_callbacks(
            on_connected=_on_connected,
            on_disconnected=_on_disconnected,
        )

        if self._voice_manager:
            self._client_manager.voice_manager = self._voice_manager

    def _build_voice_manager(self, settings: DiscordPluginSettings) -> Optional[VoiceManager]:
        """根据配置创建 VoiceManager 实例（含 TTS/STT Provider）。"""
        voice_cfg = settings.voice
        if not voice_cfg.enabled:
            return None

        tts_provider = None
        stt_provider = None
        logger = self.ctx.logger

        if voice_cfg.tts_provider == "siliconflow":
            from .src.voice.tts.siliconflow_tts import SiliconFlowTTSProvider
            tts_provider = SiliconFlowTTSProvider(settings.siliconflow_tts, logger)
        elif voice_cfg.tts_provider == "gptsovits":
            from .src.voice.tts.gptsovits_tts import GPTSoVITSTTSProvider
            tts_provider = GPTSoVITSTTSProvider(settings.gptsovits_tts, logger)
        elif voice_cfg.tts_provider == "minimax":
            from .src.voice.tts.minimax_tts import MiniMaxTTSProvider
            tts_provider = MiniMaxTTSProvider(settings.minimax_tts, logger)

        if voice_cfg.stt_provider == "siliconflow_sensevoice":
            from .src.voice.stt.siliconflow_stt import SiliconFlowSTTProvider
            stt_provider = SiliconFlowSTTProvider(settings.siliconflow_stt, logger)
        elif voice_cfg.stt_provider == "aliyun":
            from .src.voice.stt.aliyun_stt import AliyunSTTProvider
            stt_provider = AliyunSTTProvider(settings.aliyun_stt, logger)
        elif voice_cfg.stt_provider == "tencent":
            from .src.voice.stt.tencent_stt import TencentSTTProvider
            stt_provider = TencentSTTProvider(settings.tencent_stt, logger)

        platform_name = settings.platform.platform_name
        gateway_name = DISCORD_GATEWAY_NAME
        gateway_cap = self.ctx.gateway

        async def _on_stt_result(member: Any, text: str) -> None:
            timestamp = time.time()
            user_info = UserInfo(
                platform=platform_name,
                user_id=str(member.id),
                user_nickname=member.display_name,
                user_cardname=getattr(member, "nick", None),
            )
            voice_state = getattr(member, "voice", None)
            channel = getattr(voice_state, "channel", None)
            group_info = None
            if channel and getattr(channel, "guild", None):
                group_info = GroupInfo(
                    platform=platform_name,
                    group_id=str(channel.id),
                    group_name=f"{channel.name} @ {channel.guild.name}",
                )
            format_info = FormatInfo(
                content_format=["text"],
                accept_format=["text", "image", "emoji", "reply", "voice", "command", "file", "video"],
            )
            message_info = BaseMessageInfo(
                platform=platform_name,
                message_id=f"voice-{member.id}-{int(timestamp * 1000)}",
                time=timestamp,
                user_info=user_info,
                group_info=group_info,
                format_info=format_info,
                additional_config={"discord_voice_output": True} if group_info else None,
            )
            msg = MessageBase(
                message_info=message_info,
                message_segment=Seg(type="text", data=text),
                raw_message=text,
            )
            try:
                handler = self._message_handler
                if handler is None:
                    logger.error("Discord 入站消息处理器未初始化，无法上报 STT 结果")
                    return
                await gateway_cap.route_message(
                    gateway_name,
                    handler.build_host_message_dict(msg),
                    external_message_id=message_info.message_id,
                    dedupe_key=message_info.message_id,
                )
            except Exception as exc:
                logger.error(f"发送 STT 结果到 MaiCore 失败: {exc}")

        vm = VoiceManager(
            bot=None,  # type: ignore[arg-type]  # will be set in _on_connected
            logger=logger,
            voice_mode=voice_cfg.voice_mode,
            fixed_channel_id=voice_cfg.fixed_channel_id,
            auto_channel_list=voice_cfg.auto_channel_list,
            idle_timeout_sec=voice_cfg.idle_timeout_sec,
            tts_provider=tts_provider,
            stt_provider=stt_provider,
            on_stt_result=_on_stt_result,
            enable_vad=voice_cfg.enable_vad,
            vad_threshold_db=voice_cfg.vad_threshold_db,
            vad_deactivation_delay_ms=voice_cfg.vad_deactivation_delay_ms,
        )
        return vm

    async def _stop_connection(self) -> None:
        """关闭语音模块、取消后台客户端任务、停止客户端管理器并上报断开状态。"""
        if self._voice_manager is not None:
            await self._voice_manager.close()
            self._voice_manager = None

        if self._client_task and not self._client_task.done():
            self._client_task.cancel()
            try:
                await self._client_task
            except asyncio.CancelledError:
                pass
            self._client_task = None

        if self._client_manager is not None:
            await self._client_manager.stop()
            self._client_manager = None

        if self._runtime_state is not None:
            await self._runtime_state.report_disconnected()


def create_plugin() -> DiscordAdapterPlugin:
    """供 MaiBot SDK 入口调用的插件工厂，返回新的适配器实例。

    Returns:
        DiscordAdapterPlugin: 未加载状态下的 ``DiscordAdapterPlugin`` 实例。
    """
    return DiscordAdapterPlugin()
