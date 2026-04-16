<div align="center">

# MaiBot Discord Adapter

<p>
  <a href="https://discord.gg/KArcrcdWVt">
    <img src="https://img.shields.io/badge/Discord-Maiwithu-5865F2?style=flat&logo=discord&logoColor=white" alt="Discord">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="License">
  </a>
</p>

MaiBot 的 Discord 平台适配器插件，让你的 MaiBot 能够在 Discord 上运行。

> [MaiBot 原仓库](https://github.com/Mai-with-u/MaiBot)

</div>

---

## 当前能力

- 入站消息
  - Guild 频道消息
  - DM 私聊消息
  - Thread 子区消息
  - 提及、引用回复、图片、贴纸
  - Raw reaction add/remove 事件
  - 可选语音转文本 STT
- 出站消息
  - 普通文本
  - 引用回复
  - 用户/角色提及
  - 图片与部分附件
  - Reaction add/remove
  - DM / Guild / Thread 路由
  - 可选文本转语音 TTS
- 运行时能力
  - WebUI / `config.toml` 配置映射
  - 连接状态上报
  - 断线重连与健康检查
  - 子区上下文路由
  - Discord 平台消息 ID 回执回写

---

## 快速开始

1. 将插件放到 `MaiBot/plugins/maibot-discord-adapter`
2. 启动 MaiBot，让宿主加载插件
3. 在 WebUI 或插件配置中启用插件
4. 填写 `connection.token`
5. 如果要启用语音，同时打开 `voice.enabled` 和 `connection.intent_voice_states`
6. 按场景填写 `voice.fixed_channel_id` 或 `voice.auto_channel_list`

详细配置见：

- [安装配置指南](./docs/setup_guide.md)

---

## 当前语音提供方

TTS:

- `siliconflow`
- `gptsovits`
- `minimax`
- 

STT:

- `siliconflow`
- `aliyun`
- `tencent`

语音功能的最低前置条件：

- `voice.enabled = true`
- `connection.intent_voice_states = true`
- Bot 对目标语音频道拥有 `View Channel`、`Connect`、`Speak` 权限

- [语音配置指南](./docs/voice_config_guide.md)

---

## Todo List

- [ ] 增添commands统一api接口
- [ ] 由语音聊天推广到视频识别->直播?
- [ ] 欢迎提[issues](https://github.com/litroenade/MaiBot-Discord-Adapter/issues)进行补充

---

## 排障建议

由于国内Discord平台被墙，如果长时间无法连接Discord平台请尝试开启tun模式(同时推荐代理内核使用singbox)

---
## 致谢

- [@UnCLAS-Prommer](https://github.com/UnCLAS-Prommer) - MaiBot-napcat-adapter 代码参考
---

## 开源协议

本项目采用 [GPL-v3.0](LICENSE) 协议开源
