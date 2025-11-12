# MaiBot Discord Adapter 语音功能配置指南

本文档详细说明如何配置 MaiBot Discord Adapter 的语音功能，包括 TTS（文本转语音）和 STT（语音转文本）。

---

## 目录

1. [语音功能概述](#语音功能概述)
2. [基础配置](#基础配置)
3. [TTS 提供商配置](#tts-提供商配置)
   - [Azure TTS](#1-azure-tts)
   - [AI Hobbyist TTS](#2-ai-hobbyist-tts)
   - [SiliconFlow TTS](#3-siliconflow-tts)
4. [STT 提供商配置](#stt-提供商配置)
   - [Azure STT](#1-azure-stt)
   - [Aliyun STT](#2-aliyun-stt)
   - [SiliconFlow STT](#3-siliconflow-stt)
5. [故障排查](#故障排查)

---

## 语音功能概述

MaiBot Discord Adapter 支持以下语音功能：

- **TTS（文本转语音）**：将 MaiBot 的回复转换为语音并在 Discord 语音频道播放
- **STT（语音转文本）**：识别用户在语音频道的语音输入并转换为文本发送给 MaiBot


## 基础配置

在 `config.toml` 中配置语音功能基础设置：

```toml
[voice]
enabled = true  # 是否启用语音功能
voice_channel_whitelist = [1234567890123456789]  # 语音频道白名单（填入频道ID）
check_interval = 30       # 频道切换检查间隔（秒），仅多频道时生效
tts_provider = "azure"    # TTS 提供商（azure, ai_hobbyist, siliconflow）
stt_provider = "azure"    # STT 提供商（azure, aliyun, siliconflow）
```

### 获取 Discord 频道 ID

1. 启用 Discord 开发者模式：`用户设置` → `高级` → `开发者模式`
2. 右键点击语音频道 → `复制频道 ID`
3. 将 ID 填入 `voice_channel_whitelist`

### 单频道 vs 多频道模式

- **单频道**：`voice_channel_whitelist` 只有一个 ID，Bot 固定在该频道
- **多频道**：有多个 ID，Bot 自动切换到有人的频道

---

## TTS 提供商配置

### 1. Azure TTS

音质高、稳定、支持多语言

#### 配置步骤

[这里直接推荐一位老师的教程](https://bobtranslate.com/service/tts/microsoft.html)

1. 注册 Azure 账号并创建语音服务资源：https://portal.azure.com
2. 获取订阅密钥和区域信息
3. 在 `config.toml` 中配置：

```toml
[voice.azure]
subscription_key = "你的订阅密钥"  # Azure 语音服务密钥
region = "eastus"  # Azure 服务区域（eastasia, southeastasia, westus, eastus 等）
tts_voice = "zh-CN-XiaoxiaoNeural"  # TTS 语音名称
stt_language = "zh-CN"  # STT 识别语言
```

#### 可用语音列表

- 中文女声：`zh-CN-XiaoxiaoNeural`, `zh-CN-XiaoyiNeural`
- 中文男声：`zh-CN-YunxiNeural`, `zh-CN-YunyangNeural`
- 更多语音：https://learn.microsoft.com/azure/cognitive-services/speech-service/language-support

#### 常用区域

- `eastasia` - 东亚（香港）
- `southeastasia` - 东南亚（新加坡）
- `eastus` - 美国东部
- `westus` - 美国西部

---

### 2. AI Hobbyist TTS

二次元角色语音

#### 配置步骤

1. 访问官网：https://tts.acgnai.top/
2. 注册并登录
3. 点击右上角用户名，获取访问令牌（Token）
4. 在 `config.toml` 中配置：

```toml
[voice.ai_hobbyist]
api_base = "https://gsv2p.acgnai.top"  # API 基础地址
api_token = "你的访问令牌"  # 从官网获取的 Token
model_name = "原神-中文-芙宁娜_ZH"  # 语音模型名称
language = "中文"  # 语言
emotion = "默认"   # 语气
```

#### 获取可用模型

1. **方法1**：访问官网查看模型列表
2. **方法2**：程序运行时会在日志中显示可用模型
3. **方法3**：访问 API：`GET https://gsv2p.acgnai.top/models/v4`


#### 重要提示

**模型名称格式**：`游戏名-语言-角色名_后缀`
- 必须完全匹配 API 返回的模型名称
- 包括中文字符、下划线、后缀等都要一致
- 如果模型名称错误会导致 TTS 合成失败

#### 故障排查

如果出现"参数错误"或"模型不存在"：

1. 检查日志中的模型列表，确认模型名称
2. 确保 `api_token` 正确且有效
3. 检查 `language` 和 `emotion` 是否与模型匹配

---

### 3. SiliconFlow TTS

**优点**：国内服务，速度快
**缺点**：音色选择较少

#### 配置步骤

1. 访问：https://cloud.siliconflow.cn/
2. 注册并获取 API 密钥：https://cloud.siliconflow.cn/account/ak
3. 在 `config.toml` 中配置：

```toml
[voice.siliconflow]
api_key = "你的API密钥"  # SiliconFlow API 密钥
api_base = "https://api.siliconflow.cn/v1"  # API 基础地址
tts_model = "FunAudioLLM/CosyVoice2-0.5B"  # TTS 模型
tts_voice = "FunAudioLLM/CosyVoice2-0.5B:alex"  # 语音音色
response_format = "pcm"  # 音频格式（mp3, opus, wav, pcm）
sample_rate = 48000      # 采样率（Hz）
speed = 1.0              # 语速（0.25-4.0）
```

#### 可用音色

```
alex, anna, bella, benjamin, charles, claire, david, diana
```
详情请见siliconflow的api[文档](https://docs.siliconflow.cn/cn/api-reference/audio/voice-list)
---

## STT 提供商配置

### 1. Azure STT

使用与 TTS 相同的 Azure 配置：

```toml
[voice.azure]
subscription_key = "你的订阅密钥"
region = "eastus"
stt_language = "zh-CN"  # 识别语言
```

支持的语言：`zh-CN`, `en-US`, `ja-JP` 等

---

### 2. Aliyun STT

#### 配置步骤

1. 访问阿里云：https://www.aliyun.com/
2. 开通智能语音交互服务
3. 获取 AccessKey 和 AppKey
4. 在 `config.toml` 中配置：

```toml
[voice.aliyun]
access_key_id = "你的AccessKeyId"
access_key_secret = "你的AccessKeySecret"
app_key = "你的AppKey"
```

---

### 3. SiliconFlow STT

使用与 TTS 相同的配置：

```toml
[voice.siliconflow]
api_key = "你的API密钥"
stt_model = "FunAudioLLM/SenseVoiceSmall"  # STT 模型
```

---

## 故障排查

### TTS 相关问题

#### 错误：TTS 合成失败

**可能原因**：
- API 密钥错误或过期
- 模型名称不正确
- 网络连接问题
- 配额不足

**解决方法**：
1. 检查日志中的具体错误信息
2. 验证 API 密钥是否正确
3. 对于 AI Hobbyist，确认模型名称完全匹配
4. 检查网络连接和防火墙设置

#### 错误：参数错误（AI Hobbyist）

```
TTS 参数错误: 模型=xxx, 语言=xxx, 语气=xxx
```

**解决方法**：
1. 查看日志中的 "可用模型示例"
2. 确保模型名称与 API 返回的完全一致
3. 检查语言和语气参数是否与模型匹配

---

### STT 相关问题

#### 错误：无法识别语音

**可能原因**：
- 用户麦克风静音
- FFmpeg 未安装
- 音频格式不支持

**解决方法**：
1. 确认用户已取消静音
2. 安装 FFmpeg：https://ffmpeg.org/download.html
3. 检查日志中的音频处理信息

---

### 连接问题

#### Bot 无法连接到语音频道

**检查**：
1. Bot 是否有连接语音频道的权限
2. 频道 ID 是否正确
3. Discord intents 是否启用 `voice_states`

```toml
[discord.intents]
voice_states = true  # 必须启用
```

---

## 相关链接

- **Azure 语音服务**：https://azure.microsoft.com/zh-cn/services/cognitive-services/speech-services/
- **AI Hobbyist TTS**：https://tts.acgnai.top/
- **SiliconFlow**：https://cloud.siliconflow.cn/
- **FFmpeg 下载**：https://ffmpeg.org/download.html
- **Discord 开发者文档**：https://discord.com/developers/docs

---


如有问题，请查看项目 GitHub Issues 或联系开发者。

aihobbyist在线语音推理相关作者鸣谢:
        GPT-SoVITS开发者：@花儿不哭
        模型训练者：@红血球AE3803 @白菜工厂1145号员工
        推理特化包适配 & 在线推理：@AI-Hobbyist