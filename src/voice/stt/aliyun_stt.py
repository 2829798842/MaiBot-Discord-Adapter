"""阿里云 STT 提供商实现"""

import asyncio
import time
import hmac
import hashlib
import base64
from urllib.parse import quote
import aiohttp
from src.voice.base import STTProvider
from src.logger import logger as base_logger

logger = base_logger.bind(name="AliyunSTT")


class AliyunSTTProvider(STTProvider):
    """阿里云 STT 提供商"""

    def __init__(self, config):
        self.config = config
        self.access_key_id = config.access_key_id
        self.access_key_secret = config.access_key_secret
        self.app_key = config.app_key
        self.api_url = "https://nls-gateway.cn-shanghai.aliyuncs.com/stream/v1/asr"

        logger.info("阿里云 STT 初始化完成")

    def _generate_signature(self, params: dict) -> str:
        """生成签名"""
        sorted_params = sorted(params.items())
        query_string = "&".join([f"{k}={quote(str(v), safe='')}" for k, v in sorted_params])
        string_to_sign = f"GET&%2F&{quote(query_string, safe='')}"
        key = (self.access_key_secret + "&").encode('utf-8')
        signature = hmac.new(key, string_to_sign.encode('utf-8'), hashlib.sha1).digest()
        signature_base64 = base64.b64encode(signature).decode('utf-8')
        return signature_base64

    async def recognize(self, audio_data: bytes) -> str | None:
        """识别语音"""
        if not audio_data:
            return None

        if not self.access_key_id or not self.access_key_secret or not self.app_key:
            logger.error("阿里云 STT 配置不完整，请检查 access_key_id, access_key_secret, app_key")
            return None

        try:
            timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            params = {
                "AccessKeyId": self.access_key_id,
                "Action": "RunPreTrainServiceNew",
                "SignatureMethod": "HMAC-SHA1",
                "SignatureVersion": "1.0",
                "Timestamp": timestamp,
                "Version": "2018-08-28",
                "ServiceCode": "asr",
                "AppKey": self.app_key,
                "AudioFormat": "pcm",
                "SampleRate": 16000,
            }

            signature = self._generate_signature(params)
            params["Signature"] = signature

            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    self.api_url,
                    params=params,
                    data=audio_data,
                    headers={"Content-Type": "application/octet-stream"}
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        if result.get("Code") == "0":
                            text = result.get("Data", {}).get("Result", "")
                            if text:
                                logger.debug(f"识别结果: {text}")
                                return text
                            logger.debug("未识别到语音")
                            return None
                        logger.error(f"阿里云 STT 识别失败: {result.get('Message')}")
                        return None
                    error_text = await resp.text()
                    logger.error(f"阿里云 STT 请求失败: {resp.status} {error_text}")
                    return None

        except asyncio.TimeoutError:
            logger.error("阿里云 STT 请求超时")
            return None
        except aiohttp.ClientError as e:
            logger.error(f"阿里云 STT 网络错误: {e}")
            return None
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f"阿里云 STT 异常: {e}")
            return None

    async def close(self):
        """关闭资源"""
        logger.info("阿里云 STT 提供商已关闭")
