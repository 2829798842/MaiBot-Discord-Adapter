"""GPT-SoVITS TTS provider implementation."""

import asyncio
import base64
import json
from io import BytesIO
from typing import Any, Optional
from urllib.parse import quote, urlsplit, urlunsplit

import aiohttp

from ..base import TTSProvider


class GPTSoVITSTTSProvider(TTSProvider):
    """TTS provider for self-hosted GPT-SoVITS services."""

    def __init__(self, config: Any, logger: Any) -> None:
        self._logger = logger
        self._api_base: str = getattr(config, "api_base", "http://127.0.0.1:8000").rstrip("/")
        self._version: str = str(getattr(config, "version", "v4") or "v4").strip() or "v4"
        raw_template_model = str(getattr(config, "model", "") or "").strip()
        self._openai_model_id: str = (
            raw_template_model if raw_template_model.startswith("GSVI-") else ""
        )
        self._template_model_name: str = self._normalize_template_model_name(
            raw_template_model
        )
        self._template_emotion: str = str(getattr(config, "voice", "") or "").strip()
        self._cached_infer_single_target: Optional[dict[str, str]] = None
        self._text_lang: str = str(getattr(config, "text_lang", "zh") or "zh").strip()
        self._response_format: str = str(getattr(config, "response_format", "wav") or "wav").strip()
        self._speed_factor: float = float(getattr(config, "speed_factor", 1.0) or 1.0)
        self._last_error: str = ""
        self._log_initial_configuration()

    def _log_initial_configuration(self) -> None:
        openai_model_version = self._extract_openai_model_version(self._openai_model_id)
        if openai_model_version and openai_model_version != self._version:
            self._logger.warning(
                "GPT-SoVITS model/version config mismatch detected "
                f"[model={self._openai_model_id}, configured_version={self._version}, "
                f"inferred_version={openai_model_version}]"
            )

        if self._openai_model_id:
            self._logger.info(
                "GPT-SoVITS infer_single mode received an OpenAI-compatible model id in gptsovits_tts.model "
                f"({self._openai_model_id}); it is not a template model name from /models/{self._version}, "
                "so the provider will auto-discover the infer_single target."
            )

        self._logger.info(
            "GPT-SoVITS TTS initialized "
            f"[api={self._api_base}, mode=infer_single, version={self._version}, "
            f"model={self._template_model_name or 'auto'}, "
            f"emotion={self._template_emotion or 'auto'}, "
            f"openai_model={self._openai_model_id or 'none'}]"
        )

    def _determine_synthesis_modes(self) -> tuple[str, ...]:
        return ("infer_single",)

    async def synthesize(self, text: str) -> Optional[BytesIO]:
        if not text:
            self._set_last_error("empty synthesis text")
            return None

        try:
            self._clear_last_error()
            modes = self._determine_synthesis_modes()
            if not modes:
                return None
            self._logger.debug(
                "GPT-SoVITS synthesis requested "
                f"[chars={len(text)}, mode={modes[0]}, version={self._version}, "
                f"explicit_template={bool(self._template_model_name)}, response_format={self._response_format}, "
                f"speed_factor={self._speed_factor}]"
            )
            timeout = aiohttp.ClientTimeout(total=120)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                return await self._run_synthesis_sequence(session, text)
        except asyncio.TimeoutError:
            self._set_last_error("request timed out")
            self._logger.error("GPT-SoVITS TTS request timed out")
            return None
        except aiohttp.ClientError as exc:
            self._set_last_error(f"network error: {exc}")
            self._logger.error(f"GPT-SoVITS TTS network error: {exc}")
            return None
        except Exception as exc:
            self._set_last_error(f"unexpected error: {exc}")
            self._logger.error(f"GPT-SoVITS TTS error: {exc}")
            return None

    async def _run_synthesis_sequence(
        self,
        session: aiohttp.ClientSession,
        text: str,
    ) -> Optional[BytesIO]:
        self._logger.info(
            "GPT-SoVITS synthesis mode selected "
            f"[mode=infer_single, version={self._version}, text_chars={len(text)}]"
        )
        self._logger.debug(
            f"GPT-SoVITS attempting infer_single synthesis [version={self._version}]"
        )
        audio, payload = await self._synthesize_infer_single(session, text)
        if audio is not None:
            self._clear_last_error()
            return audio

        self._log_service_payload_error("infer_single", payload)
        final_message = "synthesis failed in infer_single mode"
        self._set_last_error(final_message)
        self._logger.error(f"GPT-SoVITS {final_message}")
        return None

    async def _synthesize_infer_single(
        self, session: aiohttp.ClientSession, text: str
    ) -> tuple[Optional[BytesIO], Any]:
        target = await self._resolve_infer_single_target(session)
        if target is None:
            if not self._last_error:
                self._set_last_error("infer_single target resolution failed")
            return None, None

        url = f"{self._api_base}/infer_single"
        payload = {
            "version": self._version,
            "model_name": target["model_name"],
            "prompt_text_lang": target["language"],
            "emotion": target["emotion"],
            "text": text,
            "text_lang": target["language"],
            "speed_facter": self._speed_factor,
            "media_type": self._response_format,
        }
        self._logger.debug(
            "GPT-SoVITS infer_single request prepared "
            f"[url={url}, model={target['model_name']}, language={target['language']}, emotion={target['emotion']}, "
            f"text_lang={target['language']}, media_type={self._response_format}]"
        )

        async with session.post(url, json=payload) as resp:
            if resp.status != 200:
                response_text = await resp.text()
                self._set_last_error(
                    f"infer_single request failed: HTTP {resp.status} {response_text[:200]}"
                )
                self._logger.error(
                    f"GPT-SoVITS infer_single request failed: {resp.status} {response_text}"
                )
                return None, None
            return await self._extract_audio_payload(
                session=session,
                resp=resp,
                request_name="infer_single",
                original_text=text,
            )

    async def _extract_audio_payload(
        self,
        session: aiohttp.ClientSession,
        resp: aiohttp.ClientResponse,
        request_name: str,
        original_text: str,
    ) -> tuple[Optional[BytesIO], Any]:
        data = await resp.read()
        content_type = (resp.headers.get("Content-Type") or "").lower()
        self._logger.debug(
            f"GPT-SoVITS {request_name} response received "
            f"[status={resp.status}, content_type={content_type or '<empty>'}, bytes={len(data)}]"
        )

        if self._looks_like_audio_payload(data, content_type):
            self._logger.debug(
                f"GPT-SoVITS {request_name} synthesis succeeded: {original_text[:50]}... ({len(data)} bytes)"
            )
            return BytesIO(data), None

        payload = self._try_parse_response_payload(data)
        audio_bytes = self._extract_audio_bytes(payload)
        if audio_bytes is not None:
            self._logger.debug(
                f"GPT-SoVITS {request_name} synthesis succeeded with embedded audio: "
                f"{original_text[:50]}... ({len(audio_bytes)} bytes)"
            )
            return BytesIO(audio_bytes), payload

        result_path = self._extract_result_path(payload)
        if result_path:
            self._logger.debug(
                f"GPT-SoVITS {request_name} response returned output path: {result_path}"
            )
            downloaded = await self._download_output(session, result_path)
            if downloaded is not None:
                self._logger.debug(
                    f"GPT-SoVITS {request_name} synthesis succeeded via output path: "
                    f"{original_text[:50]}..."
                )
            return downloaded, payload

        if payload is None:
            self._set_last_error(f"{request_name} returned neither audio nor structured payload")
        return None, payload

    async def _download_output(
        self, session: aiohttp.ClientSession, result_path: str
    ) -> Optional[BytesIO]:
        if result_path.startswith(("http://", "https://")):
            url = self._normalize_output_url(result_path)
        else:
            normalized = result_path.strip().lstrip("/")
            if normalized.startswith("outputs/"):
                normalized = normalized[len("outputs/") :]
            url = f"{self._api_base}/outputs/{quote(normalized, safe='/')}"
        self._logger.debug(f"GPT-SoVITS downloading synthesized output from: {url}")

        async with session.get(url) as resp:
            if resp.status != 200:
                response_text = await resp.text()
                self._set_last_error(
                    f"output download failed: HTTP {resp.status} {response_text[:200]}"
                )
                self._logger.error(
                    f"GPT-SoVITS output download failed: {resp.status} {response_text}"
                )
                return None
            data = await resp.read()
            if not data:
                self._set_last_error("output download returned empty body")
                self._logger.error("GPT-SoVITS output download returned empty body")
                return None
            return BytesIO(data)

    async def _resolve_infer_single_target(
        self, session: aiohttp.ClientSession
    ) -> Optional[dict[str, str]]:
        if self._cached_infer_single_target is not None:
            self._logger.debug(
                "GPT-SoVITS using cached infer_single target "
                f"[model={self._cached_infer_single_target['model_name']}, "
                f"language={self._cached_infer_single_target['language']}, "
                f"emotion={self._cached_infer_single_target['emotion']}]"
            )
            return self._cached_infer_single_target

        url = f"{self._api_base}/models/{quote(self._version, safe='')}"
        self._logger.debug(f"GPT-SoVITS requesting infer_single model list: {url}")
        async with session.get(url) as resp:
            if resp.status != 200:
                response_text = await resp.text()
                self._set_last_error(
                    f"model list request failed: HTTP {resp.status} {response_text[:200]}"
                )
                self._logger.warning(
                    f"GPT-SoVITS model list request failed: {resp.status} {response_text}"
                )
                return None
            try:
                payload = json.loads(await resp.text())
            except json.JSONDecodeError:
                self._set_last_error("model list response is not valid JSON")
                self._logger.warning("GPT-SoVITS model list response is not valid JSON")
                return None

        models = payload.get("models")
        if not isinstance(models, dict) or not models:
            self._set_last_error("model list is empty or malformed")
            self._logger.warning("GPT-SoVITS model list is empty or malformed")
            return None
        self._logger.debug(
            f"GPT-SoVITS model list loaded successfully [model_count={len(models)}]"
        )

        model_name = self._pick_model_name(models)
        if model_name is None:
            return None

        languages = models.get(model_name)
        if not isinstance(languages, dict) or not languages:
            self._set_last_error(f"model '{model_name}' has no available languages")
            self._logger.warning(f"GPT-SoVITS model '{model_name}' has no available languages")
            return None

        language = self._pick_language(languages)
        if language is None:
            self._set_last_error(f"no language could be selected for model '{model_name}'")
            return None

        emotions = languages.get(language)
        if not isinstance(emotions, list) or not emotions:
            self._set_last_error(
                f"model '{model_name}' language '{language}' has no emotions"
            )
            self._logger.warning(
                f"GPT-SoVITS model '{model_name}' language '{language}' has no emotions"
            )
            return None

        emotion = self._pick_emotion(emotions)
        self._logger.info(
            f"GPT-SoVITS infer_single target resolved: model={model_name} language={language} emotion={emotion}"
        )
        self._cached_infer_single_target = {
            "model_name": model_name,
            "language": language,
            "emotion": emotion,
        }
        return self._cached_infer_single_target

    def _pick_model_name(self, models: dict[str, Any]) -> Optional[str]:
        configured = self._template_model_name
        if configured:
            if configured in models:
                return configured
            fuzzy_matches = [
                name for name in models if configured in name or name.endswith(configured)
            ]
            if len(fuzzy_matches) == 1:
                return fuzzy_matches[0]
            self._logger.warning(
                f"Configured GPT-SoVITS model '{configured}' not found in /models/{self._version}"
            )

        if len(models) == 1:
            return next(iter(models.keys()))

        self._set_last_error(
            "infer_single fallback found multiple models; configure gptsovits_tts.model explicitly"
        )
        self._logger.error(
            "GPT-SoVITS infer_single fallback found multiple models; please configure gptsovits_tts.model explicitly"
        )
        return None

    def _pick_language(self, languages: dict[str, Any]) -> Optional[str]:
        preferred = self._map_language_alias(self._text_lang)
        if preferred and preferred in languages:
            return preferred
        if len(languages) == 1:
            return next(iter(languages.keys()))
        self._logger.warning(
            "GPT-SoVITS infer_single fallback found multiple languages; defaulting to the first available one"
        )
        return next(iter(languages.keys()))

    def _pick_emotion(self, emotions: list[Any]) -> str:
        normalized = [str(item).strip() for item in emotions if str(item).strip()]
        preferred = self._template_emotion
        if preferred and preferred in normalized:
            return preferred
        for candidate in ("默认", "default", "Default"):
            if candidate in normalized:
                return candidate
        return normalized[0]

    def _log_service_payload_error(self, request_name: str, payload: Any) -> None:
        if payload is None:
            return
        message = self._extract_service_error_message(payload)
        if message:
            self._set_last_error(f"{request_name} service error: {message}")
            self._logger.error(f"GPT-SoVITS {request_name} service error: {message}")
            return
        preview = self._build_payload_preview(payload, b"")
        self._set_last_error(f"{request_name} response could not be parsed as audio: {preview}")
        self._logger.error(
            f"GPT-SoVITS {request_name} response could not be parsed as audio. preview={preview}"
        )

    def _normalize_output_url(self, url: str) -> str:
        output = urlsplit(url)
        if output.hostname not in {"0.0.0.0", "localhost"}:
            return url

        base = urlsplit(self._api_base)
        if not base.netloc:
            return url
        return urlunsplit(
            (
                base.scheme or output.scheme,
                base.netloc,
                output.path,
                output.query,
                output.fragment,
            )
        )

    def get_last_error(self) -> str:
        """Return the most recent provider-level failure reason for outer diagnostics."""
        return self._last_error

    def _set_last_error(self, message: str) -> None:
        self._last_error = str(message or "").strip()

    def _clear_last_error(self) -> None:
        self._last_error = ""

    @staticmethod
    def _looks_like_audio_payload(data: bytes, content_type: str) -> bool:
        if not data:
            return False
        if content_type.startswith("audio/") or content_type == "application/octet-stream":
            return True
        if data.startswith(b"RIFF") or data.startswith(b"ID3"):
            return True
        if data.startswith(b"OggS") or data.startswith(b"fLaC"):
            return True
        return False

    @staticmethod
    def _try_parse_response_payload(data: bytes) -> Any:
        text = data.decode("utf-8", errors="ignore").strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def _extract_service_error_message(self, payload: Any) -> str:
        if isinstance(payload, dict):
            for key in ("msg", "message", "detail", "error", "result", "data"):
                if key not in payload:
                    continue
                message = self._extract_service_error_message(payload.get(key))
                if message:
                    return message
            return ""

        if isinstance(payload, list):
            for item in payload:
                message = self._extract_service_error_message(item)
                if message:
                    return message
            return ""

        if payload is None:
            return ""

        text = str(payload).strip()
        if not text:
            return ""
        return text

    def _extract_audio_bytes(self, payload: Any) -> Optional[bytes]:
        if isinstance(payload, dict):
            for key in ("audio", "audio_base64", "audio_data", "data"):
                value = payload.get(key)
                audio_bytes = self._extract_audio_bytes(value)
                if audio_bytes is not None:
                    return audio_bytes
            return None

        if isinstance(payload, list):
            for item in payload:
                audio_bytes = self._extract_audio_bytes(item)
                if audio_bytes is not None:
                    return audio_bytes
            return None

        if not isinstance(payload, str):
            return None

        candidate = payload.strip()
        if not candidate:
            return None
        if self._extract_result_path(candidate):
            return None

        try:
            return base64.b64decode(candidate, validate=True)
        except Exception:
            return None

    def _extract_result_path(self, payload: Any) -> Optional[str]:
        if isinstance(payload, dict):
            for key in (
                "result_path",
                "path",
                "output_path",
                "audio_path",
                "download_path",
                "url",
                "audio_url",
                "data",
                "result",
            ):
                if key not in payload:
                    continue
                result = self._extract_result_path(payload.get(key))
                if result:
                    return result
            return None

        if isinstance(payload, list):
            for item in payload:
                result = self._extract_result_path(item)
                if result:
                    return result
            return None

        if not isinstance(payload, str):
            return None

        value = payload.strip().strip('"')
        if not value:
            return None
        if value.startswith(("http://", "https://", "/outputs/", "outputs/")):
            return value
        if value.lower().endswith((".wav", ".mp3", ".ogg", ".flac", ".pcm")):
            return value
        return None

    @staticmethod
    def _build_payload_preview(payload: Any, raw_data: bytes) -> str:
        if payload is not None:
            preview = str(payload)
        else:
            preview = raw_data[:200].decode("utf-8", errors="ignore")
        preview = preview.replace("\r", " ").replace("\n", " ")
        return preview[:200]

    @staticmethod
    def _normalize_template_model_name(value: Any) -> str:
        text = str(value or "").strip()
        if not text or text.startswith("GSVI-"):
            return ""
        return text

    @staticmethod
    def _extract_openai_model_version(value: str) -> str:
        model_id = str(value or "").strip()
        if not model_id.startswith("GSVI-"):
            return ""

        suffix = model_id[len("GSVI-") :].strip()
        if not suffix:
            return ""
        return suffix if suffix.startswith("v") else f"v{suffix}"

    @staticmethod
    def _map_language_alias(value: Any) -> Optional[str]:
        text = str(value or "").strip()
        if not text:
            return None

        alias_map = {
            "zh": "中文",
            "中文": "中文",
            "cn": "中文",
            "en": "英文",
            "英文": "英文",
            "english": "英文",
            "ja": "日文",
            "日文": "日文",
            "jp": "日文",
            "ko": "韩文",
            "韩文": "韩文",
            "kr": "韩文",
            "yue": "粤语",
            "粤语": "粤语",
            "cantonese": "粤语",
        }
        return alias_map.get(text.lower(), text)

    async def close(self) -> None:
        self._logger.info("GPT-SoVITS TTS provider closed")
