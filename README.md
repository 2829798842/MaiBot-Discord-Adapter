<div align="center">

# MaiBot Discord Adapter

<p>
  <a href="https://discord.gg/ue4xJw7s">
    <img src="https://img.shields.io/badge/Discord-MaiBot社区-5865F2?style=flat&logo=discord&logoColor=white" alt="Discord">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="License">
  </a>
</p>

MaiBot 的 Discord 平台适配器插件，让你的 MaiBot 能够在 Discord 上运行。

> [MaiBot 原仓库](https://github.com/Mai-with-u/MaiBot)

</div>

---

## ✨ 功能特性

### 📨 消息处理
- [x] 文本消息收发
- [x] 图片消息处理
- [x] Emoji / 贴纸消息识别
- [x] 引用回复支持
- [x] 子区 (Thread) 消息处理
- [x] 子区记忆继承（可配置独立/共享上下文）
- [x] **TTS（文本转语音）**
  - Azure TTS
  - AI Hobbyist TTS（二次元角色语音）
  - SiliconFlow TTS
- [x] **STT（语音转文本）**
  - Azure STT
  - Aliyun STT  
  - SiliconFlow STT
- [x] **语音频道管理**
  - 单频道固定模式
  - 多频道自动切换
  - 麦克风状态检测

### 🔐 权限控制
- [x] 服务器黑白名单
- [x] 频道黑白名单
- [x] 用户黑白名单
- [x] 子区权限继承

---

## 📦 安装方式

### 作为 MaiBot 插件安装

1. **克隆到 MaiBot 的 plugins 目录**

```bash
cd /path/to/MaiBot/plugins
git clone https://github.com/2829798842/MaiBot-Discord-Adapter.git
```

2. **启动 MaiBot**

插件会自动被加载，依赖会在首次启动时自动安装。

3. **配置插件**

编辑 `plugins/MaiBot-Discord-Adapter/config.toml`，设置你的 Discord Bot Token：

```toml
[discord]
token = "你的Discord Bot Token"
```

4. **重启 MaiBot**

配置完成后重启 MaiBot，Discord 适配器将自动启动。

---

## 📚 详细文档

- **[安装配置指南](docs/setup_guide.md)** - 完整的安装步骤和配置说明
- **[语音功能配置](docs/voice_config_guide.md)** - TTS/STT 语音功能配置教程

---

## ⚙️ 配置说明

配置文件位于 `plugins/MaiBot-Discord-Adapter/config.toml`

### Discord 设置

```toml
[discord]
token = "你的Discord Bot Token"

[discord.intents]
messages = true           # 消息权限
guilds = true             # 服务器权限
dm_messages = true        # 私信权限
message_content = true    # 消息内容权限（必须启用）
voice_states = true       # 语音状态权限（语音功能需要）

[discord.retry]
retry_delay = 5                    # 断线重试间隔（秒）
connection_check_interval = 30     # 连接检查间隔（秒）
```

### 权限控制

```toml
[chat]
# 名单类型: "whitelist" 仅允许名单内 / "blacklist" 屏蔽名单内
guild_list_type = "blacklist"
guild_list = []                          # 服务器ID列表

channel_list_type = "blacklist"
channel_list = []                        # 频道ID列表

user_list_type = "blacklist"
user_list = []                           # 用户ID列表

allow_thread_interaction = true          # 允许子区交互
inherit_channel_permissions = true       # 子区继承父频道权限
inherit_channel_memory = true            # 子区继承父频道记忆
```

### MaiBot 连接设置

```toml
[maibot_server]
host = "127.0.0.1"                       # MaiBot Core 地址
port = 8000                              # MaiBot Core 端口
platform_name = "discord_bot_instance_1" # 平台标识符
```

### 语音设置

```toml
[voice]
enabled = false                          # 是否启用语音
tts_provider = "azure"                   # TTS 提供商
stt_provider = "azure"                   # STT 提供商
voice_channel_whitelist = []             # 语音频道白名单
check_interval = 30                      # 频道切换检查间隔

# Azure 配置
[voice.azure]
subscription_key = ""
region = "eastasia"
tts_voice = "zh-CN-XiaoxiaoNeural"
stt_language = "zh-CN"

# SiliconFlow 配置  
[voice.siliconflow]
api_key = ""
api_base = "https://api.siliconflow.cn/v1"
```

---

## 🤖 创建 Discord Bot


---

## 📁 项目结构

```
MaiBot-Discord-Adapter/
├── plugin.py              # 插件入口（MaiBot 插件系统集成）
├── config.toml            # 配置文件
├── dependence_examine.py  # 依赖检查模块
├── src/
│   ├── config/            # 配置管理
│   ├── recv_handler/      # 消息接收处理
│   │   ├── discord_client.py   # Discord 客户端
│   │   └── message_handler.py  # 消息处理器
│   ├── send_handler/      # 消息发送处理
│   ├── voice/             # 语音功能
│   ├── mmc_com_layer.py   # MaiBot 通信层
│   └── background_tasks.py # 后台任务
└── docs/                  # 文档
```

## 📋 TODO

- [ ] 完善日志，修复CLI用户无法正常查看日志的问题
- [ ] 完善语音逻辑(gsv等)
- [ ] 插件头像自定义
- [ ] Commands 支持

---
### 附录

该插件目前初步实现插件化，如果有具体问题或者想要实现的功能欢迎提issue


---
## 🙏 致谢

- [@UnCLAS-Prommer](https://github.com/UnCLAS-Prommer) - napcat-adapter 代码参考
- AI Hobbyist 在线语音推理相关作者：
  - GPT-SoVITS 开发者：@花儿不哭
  - 模型训练者：@红血球AE3803 @白菜工厂1145号员工
  - 推理特化包适配 & 在线推理：@AI-Hobbyist
- 所有贡献者和用户的支持

---

## 📄 开源协议

本项目采用 [GPLv3](LICENSE) 协议开源
