# MaiBot Discord Adapter

`maibot-discord-adapter` 是基于 MaiBot Plugin SDK 的 Discord 平台适配器。
它负责把 Discord 与 MaiBot Host 之间的消息、路由、回执、线程上下文、Reaction 和语音能力接起来。

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

## 快速开始

1. 将插件放到 `MaiBot/plugins/maibot-discord-adapter`
2. 启动 MaiBot，让宿主加载插件
3. 在 WebUI 或插件配置中启用插件
4. 填写 `connection.token`
5. 如果要启用语音，同时打开 `voice.enabled` 和 `connection.intent_voice_states`
6. 按场景填写 `voice.fixed_channel_id` 或 `voice.auto_channel_list`

## 配置结构

当前配置模型和 WebUI 表单以 [`config.py`](./config.py) 为准，主要分为：

- `plugin`
  - 插件开关与配置版本
- `connection`
  - Bot Token、Discord intents、重试和连接检查
- `chat`
  - guild / channel / thread / user 黑白名单
  - 子区是否允许互动
  - 子区是否继承父频道权限与记忆
- `platform`
  - 平台标识，默认 `discord`
- `filters`
  - 是否忽略自身消息、忽略其他 bot 消息
- `voice`
  - 语音模式、频道列表、VAD、是否在语音频道同步发文字
- `siliconflow_tts` / `gptsovits_tts` / `minimax_tts`
  - 当前支持的 TTS 提供方配置
- `siliconflow_stt` / `aliyun_stt` / `tencent_stt`
  - 当前支持的 STT 提供方配置

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

详细配置见：

- [安装配置指南](./docs/setup_guide.md)
- [语音配置指南](./docs/voice_config_guide.md)

## 排障建议

如果出现“收得到消息但发不出去”或 “DM 失败”等问题，优先检查：

- `connection.token` 是否有效
- `intent_message_content` 是否启用
- `intent_dm_messages` 是否启用
- Bot 是否已经被邀请进目标服务器
- 目标频道权限是否允许发送消息
- 插件是否已经成功加载并处于启用状态

如果出现“无法解析目标频道”，优先关注：

- 这条消息是 Guild 还是 DM
- `platform_io_target_group_id` / `platform_io_target_user_id` 是否正确继承
- 插件日志里的 route metadata、目标 ID、频道类型与权限检查结果

如果出现“无法进入语音频道”，优先关注：

- `voice.enabled` 是否已经开启
- `fixed_channel_id` 填的是不是真正的语音频道 ID，而不是分类 ID
- `intent_voice_states` 是否开启
- Bot 在该频道的 `Connect` / `Speak` 权限是否完整

## 许可证

[GPL-3.0](LICENSE)
