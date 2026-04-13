# 语音配置指南

这份文档只说明 `maibot-discord-adapter` 当前已经实现的语音配置项。
其中 GPT-SoVITS 现在统一走新版 GSV FastAPI，同时兼容 classic 权重接口和 infer_single 模板模型接口；如果 classic 没配完整，或 classic 请求失败，运行时会带日志自动回退到 infer_single 模板模型链路。

## 总览

语音功能由三层配置组成：

1. `[voice]`
   控制是否启用语音、频道模式、TTS/STT 提供商、VAD 等总开关。
2. `[xxx_tts]`
   控制具体的文本转语音服务。
3. `[xxx_stt]`
   控制具体的语音转文本服务。

如果只是先把 Discord 语音播报跑起来，最少需要：

- `voice.enabled = true`
- `connection.intent_voice_states = true`
- `voice.tts_provider = "gptsovits"` 或其他 TTS
- 语音频道配置正确

## `[voice]` 关键项

```toml
[voice]
enabled = true
voice_mode = "fixed"
fixed_channel_id = "123456789012345678"
auto_channel_list = []
idle_timeout_sec = 300
tts_provider = "gptsovits"
stt_provider = "siliconflow_sensevoice"
enable_vad = true
vad_threshold_db = -50.0
vad_deactivation_delay_ms = 500
send_text_in_voice = false
```

字段说明：

- `voice_mode = "fixed"`
  常驻一个语音频道，使用 `fixed_channel_id`
- `voice_mode = "auto"`
  在 `auto_channel_list` 候选频道里自动进出
- `fixed_channel_id`
  必须填写真正的语音频道 ID，而不是频道分类 ID
- `send_text_in_voice`
  开启后，TTS 播报时还会往文字区同步发一份文本，适合调试

## 语音接入前置条件

如果现在“就是进不了语音频道”，优先检查下面四项：

- `voice.enabled = true`
- `connection.intent_voice_states = true`
- `voice.voice_mode = "fixed"` 时，`voice.fixed_channel_id` 是否真的是语音频道 ID
- Bot 在目标语音频道是否有 `View Channel`、`Connect`、`Speak` 权限

当前适配器会先尝试从缓存取频道，取不到时再回退到 Discord API `fetch_channel()`，并同时支持普通语音频道和 Stage Channel。

## 其他 TTS 默认建议

### SiliconFlow

推荐先用这组更稳的默认值：

```toml
[siliconflow_tts]
api_key = ""
api_base = "https://api.siliconflow.cn/v1"
model = "fnlp/MOSS-TTSD-v0.5"
voice = "fnlp/MOSS-TTSD-v0.5:alex"
sample_rate = 32000
speed = 1.0
response_format = "wav"
```

说明：

- `wav` 是当前适配器里最稳的默认格式
- `pcm` / `wav` 不要再配成 `48000`
- 如果你确实想用 `opus`，采样率要配成 `48000`

### MiniMax

当前插件内部已经按新版 `voice_setting` / `audio_setting` 请求体发送请求。
推荐先用这组配置：

```toml
[minimax_tts]
api_key = ""
api_base = "https://api.minimax.io"
model = "speech-2.8-hd"
voice_id = "male-qn-qingse"
speed = 1.0
vol = 1.0
pitch = 0.0
audio_sample_rate = 32000
output_format = "mp3"
```

说明：

- 这里的 `output_format` 表示生成音频本身的格式，会映射到 MiniMax 的 `audio_setting.format`
- 插件内部固定请求 `hex` 响应，便于直接解码，不需要你再额外配置返回形式

## GPT-SoVITS

当前适配器里的 `gptsovits` 兼容两种常见接法：

- `POST /infer_classic`
- `POST /infer_single`

如果你跑的是新版 GSV FastAPI，并且 WebUI 里需要选择 GPT 权重、SoVITS 权重、参考音频和参考文本，通常按 classic 这一套来配；如果你的服务更偏 GSVI / 模板模型形式，就直接填模板模型名和模板情感。

```toml
[gptsovits_tts]
api_base = "http://127.0.0.1:8000"
version = "v4"
gpt_model_name = "GPT_weights_v2ProPlus/hiyohiyo-e10.ckpt"
sovits_model_name = "SoVITS_weights_v2ProPlus/hiyohiyo_e4_s264.pth"
model = ""
voice = ""
text_lang = "zh"
ref_audio_path = "D:/gsv_refs/hiyohiyo.wav"
prompt_text = "这是参考音频对应的文本"
prompt_lang = "zh"
response_format = "wav"
speed_factor = 1.0
```

注意：

- `gpt_model_name` 和 `sovits_model_name` 要填你 GSV 当前已经加载的模型名，不是随便写
- `model` 填 `/models/{version}` 返回的模板模型名；如果服务端只有一个模型，可以留空让适配器自动选择
- `voice` 填模板模型下的情感或音色，例如 `默认`；如果只有一个可选项，也可以留空
- `ref_audio_path` 是服务端能访问到的路径；如果你的 GSV 要先上传文件，再把返回路径填进来，也按服务端实际路径处理
- `prompt_text` 是参考音频里的原文，不是你当前要合成的文本
- `response_format` 推荐先用 `wav`

## 当前适配器对 GSV 的处理方式

适配器当前按下面的顺序处理：

- 如果你明确填写了 `model`，优先走模板模型接口，请求 `/infer_single`
- 如果你没有填写 `model`，并且 classic 权重已经配齐，优先请求 `/infer_classic`
- 如果 classic 权重没配完整，运行时会直接尝试 `/infer_single` 自动发现模板模型
- 如果 classic 请求失败，运行时会记录失败原因，再尝试 `/infer_single` 作为兜底
- 如果 `/models/{version}` 返回多个模板模型，而你又没有显式填写 `model`，运行时不会乱猜，会在日志里提示你手动指定

如果接口直接返回音频流，适配器会直接播放。
如果接口返回的是输出路径，适配器会继续请求 `/outputs/{result_path}` 下载音频。

## 排障建议

如果 GSV 播报失败，优先检查：

- `voice.enabled` 是否为 `true`
- `voice.tts_provider` 是否为 `gptsovits`
- `gptsovits_tts.api_base` 是否真的指向 GSV API，而不是只指向 WebUI 页面
- `gpt_model_name` / `sovits_model_name` 是否和 GSV 当前加载值一致
- `model` / `voice` 是否和 `/models/{version}` 返回内容一致
- `ref_audio_path` 是否是服务端机器上真实可访问的路径
- GSV 服务返回的是音频流，还是输出文件路径

如果你看到插件日志里出现：

- `classic mode requires gpt_model_name and sovits_model_name`
  说明你的 classic 配置里模型名没填；新版本会继续尝试 infer_single 自动发现
- `infer_single target resolved`
  说明你当前显式启用了模板模型模式，适配器已经从 `/models/{version}` 成功解析出模板模型、语言和情感
- `classic synthesis failed; falling back to infer_single`
  说明 classic 本身没跑通，适配器已经开始切到模板模型链路兜底
- `synthesis recovered via fallback mode: infer_single`
  说明最终是通过 infer_single 兜底成功拿到了音频
- `response could not be parsed as audio`
  说明服务返回格式和适配器预期不一致，需要把实际响应体抓出来再对
