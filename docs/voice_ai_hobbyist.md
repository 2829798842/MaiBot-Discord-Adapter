# AI Hobbyist TTS 集成说明

本文档说明 Discord Adapter 中 AI Hobbyist TTS 提供商的配置方法、接口调用流程以及常见问题。

## 平台概览

- 官网入口：<https://tts.acgnai.top/>
- API 基础路径：`https://gsv2p.acgnai.top`
- 当前适配版本：GPT-SoVITS `v4`
- 核心接口：
  - `GET /models/v4`：返回可用模型、语言、语气列表。
  - `POST /infer_single`：按照情感参数合成单段语音。
  - `GET /outputs/{result_path}`：下载合成产生的音频文件。

> 温馨提示：登录官网后可在右上角用户菜单里获取 API Token。所有需要鉴权的 POST 请求均需在请求头附带 `Authorization: Bearer <token>`。

## 配置文件示例

在 `config.toml` 中启用语音功能后，复制以下配置（模板文件 `template/template_config.toml` 亦已同步更新）：

```toml
[voice]
enabled = true
voice_channel_whitelist = [123456789012345678]
check_interval = 30
tts_provider = "ai_hobbyist"

[voice.ai_hobbyist]
api_base = "https://gsv2p.acgnai.top"
api_token = "your_api_token"
model_name = "崩环三-中文-爱莉希雅"
language = "中文"
emotion = "默认"
```

参数说明：

| 字段        | 说明                                                           |
| ----------- | -------------------------------------------------------------- |
| `api_base`  | 推理服务基础地址，如无特殊需求保持默认。                       |
| `api_token` | 访问令牌，登录官网后在右上角菜单中获取。                       |
| `model_name`| 默认语音模型，可通过 `/models/v4` 接口获取最新列表。           |
| `language`  | 提示文本语言，内部会作为 `prompt_text_lang` 与 `text_lang` 使用。|
| `emotion`   | 语气参数，具体可用值随模型而定，通常包括“默认”“开心”等。      |

> 若仍保留旧配置段 `[voice.ai_tts]` 或 `tts_provider = "ai_tts"`，程序会自动向新命名迁移，但建议尽快更新。

## 请求负载与默认参数

`infer_single` 请求体基于官方文档默认值，适配层会在运行时填充以下字段：

- `version`：`v4`
- `model_name`：`config.model_name`
- `prompt_text_lang` / `text_lang`：`config.language`
- `emotion`：`config.emotion`
- `text`：待合成的文本
- `seed`：随机整数（0-999,999,999），确保每次推理存在微小变化

除上述字段外，其余参数默认值与官方文档一致，例如 `top_k=10`、`temperature=1`、`media_type="wav"` 等。若需要自定义这些值，可在 `src/voice/tts/ai_hobbyist_tts.py` 的 `BASE_PAYLOAD_TEMPLATE` 中调整。

## 模型列表缓存

- 第一次调用 `/models/v4` 后会把响应缓存到进程内存，避免频繁访问。
- 若需刷新列表，可重启 Adapter 或在代码中清空 `AITTSProvider._models_cache`。

## 常见错误排查

| 问题现象                              | 排查建议                                                   |
| ------------------------------------- | ---------------------------------------------------------- |
| 日志显示 `Token 未配置，无法使用`     | 确保 `api_token` 已填写且未过期。                         |
| 响应 `参数错误` 或缺少 `audio_url`     | 检查模型、语言、语气组合是否正确；尝试换用官网确认可用模型。|
| 请求超时 / 网络错误                   | 核实网络连通性；适当增大 `INFER_TIMEOUT_SECONDS`。         |
| 下载音频失败                          | 再次确认 `audio_url` 地址可访问；部分代理环境需忽略证书。  |

## 进一步的自定义

- 若需要批量推理，可研究 `POST /infer_multi` 或 `POST /infer_classic`。当前代码默认使用 `infer_single`。
- 若希望使用 OpenAI 兼容接口，可扩展 `POST /v1/audio/speech` 调用并实现新的 TTS Provider。
- 如果要支持动态调整参数，可将 `BASE_PAYLOAD_TEMPLATE` 中的值暴露到配置文件，并在 `VoiceConfig` 中添加相应字段。

欢迎根据业务需求继续扩展。如有改动建议，记得同步更新本文档以及 `template/template_config.toml`。