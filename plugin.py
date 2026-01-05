import asyncio
import traceback
from typing import List, Tuple, Type, Optional
from src.common.logger import get_logger
from src.plugin_system import (
    BasePlugin,
    BaseEventHandler,
    EventType,
    register_plugin,
    ComponentInfo,
    ConfigField,
)

logger = get_logger("discord_adapter")

_shutdown_event: Optional[asyncio.Event] = None
_adapter_tasks: List[asyncio.Task] = []
_plugin_instance: Optional["DiscordAdapterPlugin"] = None


class DiscordAdapterStartEventHandler(BaseEventHandler):
    """在 MaiBot 启动时拉起 Discord 适配器"""

    event_type = EventType.ON_START
    handler_name = "discord_adapter_on_start"
    handler_description = "在 MaiBot 启动时启动 Discord 适配器"
    weight = 100

    async def execute(self, message):
        global _plugin_instance

        if _plugin_instance is None:
            logger.error("DiscordAdapterPlugin 实例未就绪，无法启动 Discord 适配器")
            return False, True, None, None, None

        if getattr(_plugin_instance, "_adapter_running", False):
            logger.info("Discord 适配器已在运行，跳过启动")
            return True, True, None, None, None

        logger.info("ON_START 触发：启动 Discord 适配器")
        ok = await _plugin_instance._initialize_adapter()
        return ok, True, None, None, None


class DiscordAdapterStopEventHandler(BaseEventHandler):
    """在 MaiBot 停止时关闭 Discord 适配器"""

    event_type = EventType.ON_STOP
    handler_name = "discord_adapter_on_stop"
    handler_description = "在 MaiBot 停止时关闭 Discord 适配器"
    weight = 100
    intercept_message = True

    async def execute(self, message):
        global _plugin_instance

        if _plugin_instance is None:
            return True, True, None, None, None

        if not getattr(_plugin_instance, "_adapter_running", False):
            return True, True, None, None, None

        logger.info("ON_STOP 触发：关闭 Discord 适配器")
        ok = await _plugin_instance._shutdown_adapter()
        return ok, True, None, None, None


@register_plugin
class DiscordAdapterPlugin(BasePlugin):
    """
    Discord 平台适配器插件
    """

    plugin_name: str = "discord_adapter"
    enable_plugin: bool = True
    dependencies: List[str] = []
    python_dependencies: List[str] = [
        "discord.py[voice]>=2.3.0",
        "discord-ext-voice-recv>=0.4.0",
        "aiohttp>=3.9.0",
        "tomlkit",
        "aiofiles",
    ]
    config_file_name: str = "config.toml"

    config_section_descriptions = {
        "plugin": "插件基本设置",
        "discord": "Discord Bot 设置",
        "chat": "聊天权限控制",
        "maibot_server": "MaiBot Core 连接设置",
        "voice": "语音功能设置",
    }

    config_schema: dict = {
        "plugin": {
            "enabled": ConfigField(type=bool, default=True, description="是否启用插件"),
        },
        "discord": {
            "token": ConfigField(
                type=str,
                default="your_discord_bot_token_",
                description="Discord Bot Token (必填)",
                label="Bot Token",
                example="MTE4...",
            ),
        },
        "discord.intents": {
            "messages": ConfigField(type=bool, default=True, description="消息权限 (接收普通消息)"),
            "guilds": ConfigField(type=bool, default=True, description="服务器权限 (获取服务器信息)"),
            "dm_messages": ConfigField(type=bool, default=True, description="私信权限 (接收私信)"),
            "message_content": ConfigField(type=bool, default=True, description="消息内容权限 (必须启用，否则无法读取消息内容)"),
            "voice_states": ConfigField(type=bool, default=True, description="语音状态权限 (语音功能必须启用)"),
        },
        "discord.retry": {
            "retry_delay": ConfigField(type=int, default=5, description="断线重试间隔 (秒)"),
            "connection_check_interval": ConfigField(type=int, default=30, description="连接状态检查间隔 (秒，建议30秒以上)"),
        },
        "chat": {
            "guild_list_type": ConfigField(
                type=str,
                default="blacklist",
                description="服务器黑白名单类型 (whitelist: 仅允许名单内; blacklist: 仅屏蔽名单内)",
                choices=["whitelist", "blacklist"],
                example="blacklist",
            ),
            "guild_list": ConfigField(
                type=list,
                default=[],
                description="服务器 ID 名单 (右键服务器图标 -> 复制服务器 ID)",
                example="[123456789, 987654321]",
            ),
            "channel_list_type": ConfigField(
                type=str,
                default="blacklist",
                description="频道黑白名单类型",
                choices=["whitelist", "blacklist"],
            ),
            "channel_list": ConfigField(
                type=list,
                default=[],
                description="频道 ID 名单 (右键频道名 -> 复制频道 ID)",
                example="[123456789, 987654321]",
            ),
            "thread_list_type": ConfigField(
                type=str,
                default="blacklist",
                description="子区黑白名单类型 (若inherit_channel_permissions=True则此项无效)",
                choices=["whitelist", "blacklist"],
            ),
            "thread_list": ConfigField(type=list, default=[], description="子区 ID 名单"),
            "user_list_type": ConfigField(
                type=str,
                default="blacklist",
                description="用户黑白名单类型",
                choices=["whitelist", "blacklist"],
            ),
            "user_list": ConfigField(type=list, default=[], description="用户 ID 名单"),
            "allow_thread_interaction": ConfigField(type=bool, default=True, description="是否允许在子区(Thread)中互动"),
            "inherit_channel_permissions": ConfigField(
                type=bool,
                default=True,
                description="子区是否继承父频道权限 (推荐True: 父频道允许则子区允许)",
            ),
            "inherit_channel_memory": ConfigField(
                type=bool,
                default=True,
                description="子区是否继承父频道记忆 (True: 共享上下文; False: 独立上下文)",
            ),
        },
        "maibot_server": {
            "host": ConfigField(type=str, default="127.0.0.1", description="MaiBot Core 主机地址"),
            "port": ConfigField(type=int, default=8000, description="MaiBot Core 端口"),
            "platform_name": ConfigField(type=str, default="discord_bot_instance_1", description="平台标识符 (多实例运行时需唯一)"),
        },
        "debug": {
            "level": ConfigField(
                type=str,
                default="INFO",
                description="日志输出等级",
                choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
            ),
            "log_file": ConfigField(type=str, default="logs/discord_adapter.log", description="日志文件保存路径"),
        },
        "voice": {
            "enabled": ConfigField(type=bool, default=False, description="是否启用语音功能 (需同时开启voice_states权限)"),
            "voice_channel_whitelist": ConfigField(
                type=list,
                default=[],
                description="语音频道白名单 (为空则不限制，自动跟随有人的频道)",
                example="[123456789]",
            ),
            "check_interval": ConfigField(type=int, default=30, description="语音频道自动切换检查间隔 (秒)"),
            "tts_provider": ConfigField(
                type=str,
                default="azure",
                description="TTS(语音合成)服务提供商",
                choices=["azure", "ai_hobbyist", "siliconflow"],
            ),
            "stt_provider": ConfigField(
                type=str,
                default="azure",
                description="STT(语音识别)服务提供商",
                choices=["azure", "aliyun", "siliconflow"],
            ),
        },
        "voice.azure": {
            "subscription_key": ConfigField(type=str, default="", description="Azure 语音服务密钥"),
            "region": ConfigField(type=str, default="eastasia", description="Azure 服务区域 (如 eastasia, westus)"),
            "tts_voice": ConfigField(type=str, default="zh-CN-XiaoxiaoNeural", description="TTS 发音人名称"),
            "stt_language": ConfigField(type=str, default="zh-CN", description="STT 识别语言代码"),
        },
        "voice.aliyun": {
            "access_key_id": ConfigField(type=str, default="", description="阿里云 AccessKey ID"),
            "access_key_secret": ConfigField(type=str, default="", description="阿里云 AccessKey Secret"),
            "app_key": ConfigField(type=str, default="", description="智能语音交互 App Key"),
        },
        "voice.ai_hobbyist": {
            "api_base": ConfigField(type=str, default="https://gsv2p.acgnai.top", description="GPT-SoVITS API 地址"),
            "api_token": ConfigField(type=str, default="", description="API Token (无需则留空)"),
            "model_name": ConfigField(type=str, default="原神_中文_芙宁娜_ZH", description="模型名称"),
            "language": ConfigField(type=str, default="中文", description="合成语言"),
            "emotion": ConfigField(type=str, default="默认", description="情感风格"),
        },
        "voice.siliconflow": {
            "api_key": ConfigField(type=str, default="", description="SiliconFlow API Key"),
            "api_base": ConfigField(type=str, default="https://api.siliconflow.cn/v1", description="API 基础地址"),
            "tts_model": ConfigField(type=str, default="fnlp/MOSS-TTSD-v0.5", description="TTS 模型标识"),
            "tts_voice": ConfigField(type=str, default="fnlp/MOSS-TTSD-v0.5:alex", description="TTS 音色标识"),
            "stt_model": ConfigField(type=str, default="FunAudioLLM/SenseVoiceSmall", description="STT 模型标识"),
            "response_format": ConfigField(type=str, default="pcm", description="音频返回格式 (建议 pcm)"),
            "sample_rate": ConfigField(type=int, default=44100, description="音频采样率"),
            "speed": ConfigField(type=float, default=1.0, description="语速调节 (0.1 ~ 2.0)"),
        },
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._adapter_running = False
        global _plugin_instance
        _plugin_instance = self
        
    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        """返回插件组件列表"""
        return [
            (DiscordAdapterStartEventHandler.get_handler_info(), DiscordAdapterStartEventHandler),
            (DiscordAdapterStopEventHandler.get_handler_info(), DiscordAdapterStopEventHandler),
        ]

    async def _initialize_adapter(self) -> bool:
        """初始化并启动 Discord 适配器（内部方法）"""
        global _shutdown_event, _adapter_tasks

        logger.info("正在初始化 Discord 适配器...")

        # 1. 检查依赖，缺失则询问用户是否安装
        try:
            from .dependence_examine import prompt_and_install_dependencies

            deps_ok = await prompt_and_install_dependencies()
            if not deps_ok:
                logger.error("依赖未就绪，无法启动 Discord 适配器")
                return False
        except Exception as e:
            logger.warning(f"依赖检查跳过: {e}")

        # 2. 检查配置
        token = self.get_config("discord.token", "")
        logger.debug(f"当前配置内容: {self.config}")
        logger.debug(f"获取到的 Token (前10字符): {token[:10] if token and len(token) > 10 else token}...")
        if not token or token == "your_discord_bot_token_":
            logger.error("请在插件配置中设置有效的 Discord Bot Token")
            logger.error(f"当前 Token 值: {token}")
            logger.error("配置文件路径: plugins/MaiBot-Discord-Adapter/config.toml")
            return False

        try:
            # 初始化关闭事件
            _shutdown_event = asyncio.Event()
            _adapter_tasks = []

            # !! 重要：先注入配置，再导入其他模块 !!
            # 这样确保延迟初始化的模块使用正确的配置
            self._inject_config()

            # 重置延迟初始化的实例，确保使用新配置
            from .src.mmc_com_layer import reset_router
            reset_router()

            # 导入适配器模块 (延迟导入，避免循环依赖)
            from .src.recv_handler.discord_client import discord_client, get_discord_client
            from .src.recv_handler.message_handler import message_handler
            from .src.send_handler.main_send_handler import send_handler, get_send_handler
            from .src.mmc_com_layer import mmc_start_com, get_router
            from .src.background_tasks import background_task_manager

            # 获取实际实例（触发延迟初始化）
            actual_router = get_router()
            actual_send_handler = get_send_handler()
            actual_discord_client = get_discord_client()

            # 设置消息处理器的路由器
            message_handler.router = actual_router
            message_handler.send_handler = actual_send_handler

            # 注册 MaiBot 消息处理器
            actual_router.register_class_handler(actual_send_handler.handle_message)

            # 启动 MaiBot 通信
            mmc_task = asyncio.create_task(mmc_start_com())
            _adapter_tasks.append(mmc_task)
            logger.info("MaiBot 通信任务已创建")

            # 启动消息处理器
            processor_task = asyncio.create_task(self._message_process())
            _adapter_tasks.append(processor_task)
            logger.info("消息处理任务已创建")

            # 启动 Discord 客户端
            discord_task = asyncio.create_task(actual_discord_client.start())
            _adapter_tasks.append(discord_task)
            logger.info("Discord 客户端任务已创建")

            # 等待 Discord 客户端连接
            await asyncio.sleep(2)

            # 传递语音管理器
            if actual_discord_client.voice_manager:
                actual_send_handler.voice_manager = actual_discord_client.voice_manager
                logger.info("语音管理器已连接到发送处理器")

            # 启动后台任务
            background_task_manager.register_connection_monitor(actual_discord_client)
            background_task_manager.register_reaction_event_task(actual_discord_client, message_handler)
            background_task_manager.start_all_tasks()
            logger.info("后台任务管理器已启动")

            self._adapter_running = True
            logger.info("Discord 适配器已成功启动")
            return True

        except Exception as e:
            logger.error(f"启动 Discord 适配器失败: {e}")
            logger.error(traceback.format_exc())
            return False

    async def _shutdown_adapter(self) -> bool:
        """关闭 Discord 适配器（内部方法）"""
        global _shutdown_event, _adapter_tasks

        logger.info("正在关闭 Discord 适配器...")

        try:
            if _shutdown_event:
                _shutdown_event.set()

            # 导入模块
            from .src.recv_handler.discord_client import get_discord_client
            from .src.mmc_com_layer import mmc_stop_com
            from .src.background_tasks import background_task_manager
            from .src.utils import async_task_manager

            # 停止后台任务
            try:
                background_task_manager.stop_all_tasks()
                logger.info("后台任务已停止")
            except Exception as e:
                logger.error(f"停止后台任务时出错: {e}")

            # 关闭 Discord 客户端
            try:
                actual_discord_client = get_discord_client()
                await asyncio.wait_for(actual_discord_client.stop(), timeout=10)
                logger.info("Discord 客户端已关闭")
            except asyncio.TimeoutError:
                logger.warning("Discord 客户端关闭超时")
            except Exception as e:
                logger.error(f"关闭 Discord 客户端时出错: {e}")

            # 关闭 MaiBot 通信
            try:
                await asyncio.wait_for(mmc_stop_com(), timeout=10)
                logger.info("MaiBot 通信已关闭")
            except Exception as e:
                logger.error(f"关闭 MaiBot 通信时出错: {e}")

            # 取消管理器任务
            try:
                await async_task_manager.cancel_all()
            except Exception as e:
                logger.error(f"取消任务管理器时出错: {e}")

            # 取消适配器任务
            for task in _adapter_tasks:
                if not task.done():
                    task.cancel()

            if _adapter_tasks:
                await asyncio.gather(*_adapter_tasks, return_exceptions=True)

            _adapter_tasks = []
            self._adapter_running = False
            logger.info("Discord 适配器已成功关闭")
            return True

        except Exception as e:
            logger.error(f"关闭 Discord 适配器时出错: {e}")
            return False

    def _inject_config(self):
        """将插件配置注入到适配器模块"""
        from .src.config import inject_plugin_config
        
        # 获取当前插件的完整配置
        plugin_config = self.config if hasattr(self, 'config') else {}
        
        logger.info(f"正在注入插件配置到适配器模块...")
        logger.debug(f"插件配置内容: {plugin_config}")
        
        # 检查 discord.token 是否在配置中
        discord_cfg = plugin_config.get("discord", {})
        token = discord_cfg.get("token", "")
        logger.debug(f"Discord Token (前10字符): {token[:10] if token and len(token) > 10 else token}...")
        
        inject_plugin_config(plugin_config)
        logger.info("配置注入完成")

    async def _message_process(self):
        """消息处理协程"""
        global _shutdown_event

        from .src.recv_handler.discord_client import discord_client
        from .src.recv_handler.message_handler import message_handler

        logger.info("消息处理器已启动")

        while _shutdown_event is not None and not _shutdown_event.is_set():
            try:
                message = await asyncio.wait_for(discord_client.message_queue.get(), timeout=1.0)
                logger.debug(f"开始处理消息 {message.id}")
                await message_handler.handle_discord_message(message)
                discord_client.message_queue.task_done()
                logger.debug(f"消息 {message.id} 处理完成")

            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"处理消息时发生错误: {e}")
                logger.error(traceback.format_exc())
                await asyncio.sleep(0.1)

        logger.info("消息处理器已停止")
