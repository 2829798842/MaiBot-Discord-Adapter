"""腾讯云一句话识别 STT 提供商实现。

通过腾讯云 ASR ``SentenceRecognition`` 接口上传 base64 编码的音频数据进行识别。
使用 TC3-HMAC-SHA256 签名方式。
"""

import asyncio
import base64
import datetime
import hashlib
import hmac
import json
import struct
from typing import Any, Optional

import aiohttp

from ..base import STTProvider


class TencentSTTProvider(STTProvider):
    """腾讯云一句话识别提供商。

    使用 ``asr.tencentcloudapi.com`` 的 ``SentenceRecognition`` 接口。
    上传 base64 编码的 WAV 音频（从 16kHz PCM 包装），同步返回识别结果。

    需要的配置项：
    - ``secret_id``: 腾讯云 SecretId
    - ``secret_key``: 腾讯云 SecretKey
    - ``engine``: 识别引擎，默认 ``16k_zh``
    - ``region``: 服务区域，默认 ``ap-shanghai``
    """

    def __init__(self, config: Any, logger: Any) -> None:
        """初始化腾讯云 STT 提供商。

        Args:
            config: 包含 secret_id / secret_key 等属性的配置对象。
            logger: 日志记录器。
        """
        self._logger = logger
        self._secret_id: str = getattr(config, "secret_id", "")
        self._secret_key: str = getattr(config, "secret_key", "")
        self._engine: str = getattr(config, "engine", "16k_zh")
        self._region: str = getattr(config, "region", "ap-shanghai")
        self._logger.info(f"腾讯云 STT 初始化完成 [引擎: {self._engine}]")

    async def recognize(self, audio_data: bytes) -> Optional[str]:
        """识别 PCM 音频数据。

        Args:
            audio_data: 16kHz 16-bit 单声道 PCM 字节流。

        Returns:
            识别出的文本，失败返回 None。
        """
        if not audio_data:
            return None
        if not self._secret_id or not self._secret_key:
            self._logger.error("腾讯云 STT 凭证未配置")
            return None

        wav_data = _wrap_pcm_as_wav(audio_data)
        audio_b64 = base64.b64encode(wav_data).decode("ascii")

        payload = {
            "ProjectId": 0,
            "SubServiceType": 2,
            "EngSerViceType": self._engine,
            "SourceType": 1,
            "VoiceFormat": "wav",
            "UsrAudioKey": "discord-voice",
            "Data": audio_b64,
            "DataLen": len(wav_data),
        }

        host = "asr.tencentcloudapi.com"
        action = "SentenceRecognition"
        version = "2019-06-14"

        payload_json = json.dumps(payload)
        headers = self._build_tc3_headers(host, action, version, payload_json)

        try:
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"https://{host}/", data=payload_json, headers=headers
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        response = result.get("Response", {})
                        if "Error" in response:
                            err = response["Error"]
                            self._logger.error(
                                f"腾讯云 STT 服务错误: {err.get('Code')} {err.get('Message')}"
                            )
                            return None
                        text = response.get("Result", "")
                        if text:
                            self._logger.debug(f"腾讯云 STT 识别结果: {text}")
                            return text
                        return None
                    text_err = await resp.text()
                    self._logger.error(f"腾讯云 STT 请求失败: {resp.status} {text_err}")
                    return None
        except asyncio.TimeoutError:
            self._logger.error("腾讯云 STT 请求超时")
            return None
        except aiohttp.ClientError as exc:
            self._logger.error(f"腾讯云 STT 网络错误: {exc}")
            return None
        except Exception as exc:
            self._logger.error(f"腾讯云 STT 异常: {exc}")
            return None

    async def close(self) -> None:
        """关闭并释放资源。"""
        self._logger.info("腾讯云 STT 提供商已关闭")

    def _build_tc3_headers(
        self, host: str, action: str, version: str, payload: str
    ) -> dict[str, str]:
        """构建 TC3-HMAC-SHA256 签名的请求头。

        Args:
            host: 请求域名。
            action: API Action 名称。
            version: API 版本号。
            payload: JSON 格式的请求体。

        Returns:
            包含 Authorization 签名的完整请求头字典。
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        timestamp = str(int(now.timestamp()))
        date = now.strftime("%Y-%m-%d")
        service = "asr"

        canonical_uri = "/"
        canonical_querystring = ""
        ct = "application/json; charset=utf-8"
        canonical_headers = f"content-type:{ct}\nhost:{host}\nx-tc-action:{action.lower()}\n"
        signed_headers = "content-type;host;x-tc-action"
        hashed_payload = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        canonical_request = (
            f"POST\n{canonical_uri}\n{canonical_querystring}\n"
            f"{canonical_headers}\n{signed_headers}\n{hashed_payload}"
        )

        credential_scope = f"{date}/{service}/tc3_request"
        hashed_canonical = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
        string_to_sign = f"TC3-HMAC-SHA256\n{timestamp}\n{credential_scope}\n{hashed_canonical}"

        def _hmac_sha256(key: bytes, msg: str) -> bytes:
            return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

        secret_date = _hmac_sha256(("TC3" + self._secret_key).encode("utf-8"), date)
        secret_service = _hmac_sha256(secret_date, service)
        secret_signing = _hmac_sha256(secret_service, "tc3_request")
        signature = hmac.new(
            secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        authorization = (
            f"TC3-HMAC-SHA256 Credential={self._secret_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

        return {
            "Authorization": authorization,
            "Content-Type": ct,
            "Host": host,
            "X-TC-Action": action,
            "X-TC-Version": version,
            "X-TC-Timestamp": timestamp,
            "X-TC-Region": self._region,
        }


def _wrap_pcm_as_wav(
    pcm_data: bytes,
    sample_rate: int = 16000,
    channels: int = 1,
    bits_per_sample: int = 16,
) -> bytes:
    """将裸 PCM 数据包装为完整的 WAV 文件。

    Args:
        pcm_data: 裸 PCM 字节流。
        sample_rate: 采样率，默认 16000。
        channels: 声道数，默认 1。
        bits_per_sample: 采样位数，默认 16。

    Returns:
        带有 RIFF/WAV 头的完整音频字节。
    """
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    data_size = len(pcm_data)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_size, b"WAVE",
        b"fmt ", 16, 1, channels, sample_rate,
        byte_rate, block_align, bits_per_sample,
        b"data", data_size,
    )
    return header + pcm_data
