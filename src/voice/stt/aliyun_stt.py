"""阿里云一句话识别 STT 提供商实现。

通过 NLS Gateway RESTful API 上传不超过 60 秒的 PCM 音频进行识别。
需要先获取 Access Token，再携带 Token 上传音频二进制流。
"""

import asyncio
import hashlib
import hmac
import time
from typing import Any, Optional

import aiohttp

from ..base import STTProvider


class AliyunSTTProvider(STTProvider):
    """阿里云智能语音交互一句话识别提供商。

    使用 NLS Gateway 的 ``/stream/v1/asr`` 接口，以 HTTPS POST 方式
    整段上传 16kHz 单声道 PCM 音频，同步返回识别结果。

    需要的配置项：
    - ``access_key_id``: 阿里云 AccessKey ID
    - ``access_key_secret``: 阿里云 AccessKey Secret
    - ``app_key``: 智能语音交互项目 App Key
    - ``region``: 服务区域，默认 ``cn-shanghai``
    """

    def __init__(self, config: Any, logger: Any) -> None:
        """初始化阿里云 STT 提供商。

        Args:
            config: 包含 access_key_id / access_key_secret / app_key 等属性的配置对象。
            logger: 日志记录器。
        """
        self._logger = logger
        self._access_key_id: str = getattr(config, "access_key_id", "")
        self._access_key_secret: str = getattr(config, "access_key_secret", "")
        self._app_key: str = getattr(config, "app_key", "")
        self._region: str = getattr(config, "region", "cn-shanghai")
        self._token: Optional[str] = None
        self._token_expire: float = 0
        self._logger.info(f"阿里云 STT 初始化完成 [区域: {self._region}]")

    async def recognize(self, audio_data: bytes) -> Optional[str]:
        """识别 PCM 音频数据。

        Args:
            audio_data: 16kHz 16-bit 单声道 PCM 字节流。

        Returns:
            识别出的文本，失败返回 None。
        """
        if not audio_data:
            return None
        if not self._access_key_id or not self._access_key_secret or not self._app_key:
            self._logger.error("阿里云 STT 凭证未完整配置")
            return None

        token = await self._ensure_token()
        if not token:
            return None

        gateway_host = f"nls-gateway-{self._region}.aliyuncs.com"
        url = (
            f"https://{gateway_host}/stream/v1/asr"
            f"?appkey={self._app_key}"
            f"&format=pcm"
            f"&sample_rate=16000"
            f"&enable_punctuation_prediction=true"
            f"&enable_inverse_text_normalization=true"
        )
        headers = {
            "X-NLS-Token": token,
            "Content-Type": "application/octet-stream",
        }

        try:
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, data=audio_data, headers=headers) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        status_code = result.get("status", 0)
                        if status_code == 20000000:
                            text = result.get("result", "")
                            if text:
                                self._logger.debug(f"阿里云 STT 识别结果: {text}")
                                return text
                            return None
                        self._logger.error(
                            f"阿里云 STT 服务错误: {status_code} {result.get('message', '')}"
                        )
                        return None
                    text_err = await resp.text()
                    self._logger.error(f"阿里云 STT 请求失败: {resp.status} {text_err}")
                    return None
        except asyncio.TimeoutError:
            self._logger.error("阿里云 STT 请求超时")
            return None
        except aiohttp.ClientError as exc:
            self._logger.error(f"阿里云 STT 网络错误: {exc}")
            return None
        except Exception as exc:
            self._logger.error(f"阿里云 STT 异常: {exc}")
            return None

    async def close(self) -> None:
        """关闭并释放资源。"""
        self._logger.info("阿里云 STT 提供商已关闭")

    async def _ensure_token(self) -> Optional[str]:
        """确保 Access Token 有效，过期时自动刷新。

        Returns:
            有效的 Token 字符串，获取失败返回 None。
        """
        now = time.time()
        if self._token and now < self._token_expire - 60:
            return self._token

        url = "https://nls-meta.cn-shanghai.aliyuncs.com/"
        params = {
            "Action": "CreateToken",
            "Version": "2019-02-28",
            "AccessKeyId": self._access_key_id,
            "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "Format": "JSON",
            "SignatureMethod": "HMAC-SHA1",
            "SignatureVersion": "1.0",
            "SignatureNonce": str(int(now * 1000)),
        }

        sorted_params = sorted(params.items())
        query_string = "&".join(f"{k}={_percent_encode(v)}" for k, v in sorted_params)
        string_to_sign = f"GET&{_percent_encode('/')}&{_percent_encode(query_string)}"
        sign_key = (self._access_key_secret + "&").encode("utf-8")
        signature = hmac.new(sign_key, string_to_sign.encode("utf-8"), hashlib.sha1)

        import base64
        sig_b64 = base64.b64encode(signature.digest()).decode("utf-8")
        params["Signature"] = sig_b64

        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, params=params) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        token_data = result.get("Token", {})
                        self._token = token_data.get("Id")
                        self._token_expire = token_data.get("ExpireTime", 0)
                        self._logger.debug("阿里云 Token 获取成功")
                        return self._token
                    text_err = await resp.text()
                    self._logger.error(f"阿里云 Token 获取失败: {resp.status} {text_err}")
                    return None
        except Exception as exc:
            self._logger.error(f"阿里云 Token 获取异常: {exc}")
            return None


def _percent_encode(value: str) -> str:
    """阿里云签名所需的 URL 编码（RFC 3986）。"""
    import urllib.parse
    return urllib.parse.quote(str(value), safe="")
