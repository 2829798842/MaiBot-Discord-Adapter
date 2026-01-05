# MaiBot 与 Discord 的 Adapter

MaiBot 与 Discord 的适配器。

这里是MaiBot非官方服务器，欢迎加入
[![Discord](https://img.shields.io/badge/Discord-MaiBot-5865F2?style=flat&logo=discord&logoColor=white)](https://discord.gg/cdn9k3tXm6)

[MaiBot原仓库链接](https://github.com/Mai-with-u/MaiBot)


## 功能特性

### 消息处理
- [x] 文本消息接收与发送
- [x] 图片消息处理
- [x] Emoji 消息识别
- [x] 贴纸消息处理
- [x] 引用回复支持
- [x] 子区（Thread）消息处理

### 语音功能
- [x] TTS（文本转语音）
  - 支持 Azure TTS
  - 支持 AI Hobbyist TTS（二次元角色语音）
  - 支持 SiliconFlow TTS
- [x] STT（语音转文本）
  - 支持 Azure STT
  - 支持 Aliyun STT
  - 支持 SiliconFlow STT
- [x] 语音频道管理
  - 单频道固定模式
  - 多频道自动切换
  - 麦克风状态检测

### 权限控制
- [x] 服务器黑白名单
- [x] 频道黑白名单
- [x] 用户黑白名单
- [x] 子区权限继承

#### TodoList
- [ ] gptsovits等更多tts接入
- [ ] 插件头像等

### 声明
由于[MaiBot](https://github.com/Mai-with-u/MaiBot)原仓库即将转入MaiNext开发，后续此仓库将转为插件化，将停更一段时间，等待MaiNext开发完成。

## 使用说明

详细安装与配置请参阅项目文档：[安装配置指南](docs/setup_guide.md)


## 特别鸣谢

- 感谢 [@UnCLAS-Prommer](https://github.com/UnCLAS-Prommer) 的 napcat-adapter 代码参考
- 感谢所有贡献者和用户的支持

- aihobbyist在线语音推理相关作者鸣谢:
    -  GPT-SoVITS开发者：@花儿不哭
    -   模型训练者：@红血球AE3803 @白菜工厂1145号员工
    -   推理特化包适配 & 在线推理：@AI-Hobbyist
## 开源协议

本项目采用 GPLv3 协议开源
