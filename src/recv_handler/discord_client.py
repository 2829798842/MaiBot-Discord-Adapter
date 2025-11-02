"""模块名称：Discord 客户端管理器
主要功能：管理 Discord Bot 客户端连接和事件处理
"""

import asyncio
import time
import traceback
from importlib import import_module

import discord
from maim_message import (
    BaseMessageInfo,
    FormatInfo,
    GroupInfo,
    MessageBase,
    Seg,
    UserInfo,
)

from ..logger import logger
from ..config import global_config, is_user_allowed
from ..mmc_com_layer import router

class DiscordClientManager:
    """Discord 客户端管理器
    
    负责管理 Discord Bot 的连接、事件处理和消息队列
    
    Attributes:
        client (discord.Client | None): Discord 客户端实例
        message_queue (asyncio.Queue): 消息队列
        is_connected (bool): 连接状态
        is_shutting_down (bool): 是否正在关闭
        is_reconnecting (bool): 是否正在重连
    """

    def __init__(self):
        """初始化 Discord 客户端管理器"""
        self.client = None
        self.message_queue = asyncio.Queue()
        self.is_connected = False
        self.is_shutting_down = False
        self.is_reconnecting = False
        self._reconnect_task = None
        self.voice_manager = None  # 语音管理器（将在启动时初始化）
        self._setup_client()

    def _setup_client(self):
        """设置 Discord 客户端
        
        配置 Discord 客户端的权限意图并注册事件处理器
        """
        intents = discord.Intents.default()
        discord_intents = global_config.discord.intents

        intents.messages = discord_intents.get("messages", True)
        intents.guilds = discord_intents.get("guilds", True)
        intents.dm_messages = discord_intents.get("dm_messages", True)
        intents.message_content = discord_intents.get("message_content", True)
        intents.reactions = discord_intents.get("reactions", True)
        intents.voice_states = discord_intents.get("voice_states", False)

        logger.debug(
            f"Discord 权限意图: messages={intents.messages}, guilds={intents.guilds}, "
            f"dm_messages={intents.dm_messages}, message_content={intents.message_content}, "
            f"reactions={intents.reactions}, voice_states={intents.voice_states}"
        )

        # 创建 Discord 客户端
        self.client = discord.Client(intents=intents)

        # 使用装饰器方式注册事件处理器
        @self.client.event
        async def on_ready():
            await self._on_ready()

        @self.client.event
        async def on_message(message):
            await self._on_message(message)

        @self.client.event
        async def on_error(event, *args, **kwargs):
            await self._on_error(event, *args, **kwargs)

        @self.client.event
        async def on_disconnect():
            await self._on_disconnect()

        @self.client.event
        async def on_resume():
            await self._on_resume()

        @self.client.event
        async def on_voice_state_update(member, before, after):
            await self._on_voice_state_update(member, before, after)

        logger.debug("Discord 客户端初始化完成")

    async def _on_ready(self):
        """Discord 客户端就绪事件处理器
        
        当 Discord 客户端连接成功并准备就绪时调用
        """
        self.is_connected = True
        logger.info(f"Discord 客户端已连接: {self.client.user}")
        logger.info(f"Bot 已加入 {len(self.client.guilds)} 个服务器")

        # 显示加入的服务器信息
        for guild in self.client.guilds:
            logger.debug(f"服务器: {guild.name} (ID: {guild.id})")
            # 显示前几个频道
            text_channels = guild.text_channels[:3]  # 只显示前3个频道
            for channel in text_channels:
                logger.debug(f"  - 频道: {channel.name} (ID: {channel.id})")

        logger.info("Discord 客户端准备就绪，等待消息事件...")

        # 初始化语音功能
        await self._initialize_voice()

    async def _initialize_voice(self):
        """初始化语音功能"""
        try:
            voice_config = global_config.voice
            if not voice_config.enabled:
                logger.debug("语音功能未启用")
                return

            # 动态导入语音模块
            try:
                voice_pkg = import_module("src.voice")
                voice_manager_cls = voice_pkg.VoiceManager

                azure_tts_module = import_module("src.voice.tts.azure_tts")
                azure_tts_cls = azure_tts_module.AzureTTSProvider

                azure_stt_module = import_module("src.voice.stt.azure_stt")
                azure_stt_cls = azure_stt_module.AzureSTTProvider

                ai_tts_module = import_module("src.voice.tts.ai_tts")
                ai_tts_cls = ai_tts_module.AITTSProvider

                aliyun_stt_module = import_module("src.voice.stt.aliyun_stt")
                aliyun_stt_cls = aliyun_stt_module.AliyunSTTProvider

                siliconflow_tts_module = import_module("src.voice.tts.siliconflow_tts")
                siliconflow_tts_cls = siliconflow_tts_module.SiliconFlowTTSProvider

                siliconflow_stt_module = import_module("src.voice.stt.siliconflow_stt")
                siliconflow_stt_cls = siliconflow_stt_module.SiliconFlowSTTProvider
            except (ImportError, AttributeError) as import_err:
                logger.warning(f"语音模块导入失败，跳过语音功能: {import_err}")
                logger.info("提示：如需使用语音功能，请安装依赖")
                return

            # 初始化 TTS 提供商
            tts_provider = None
            try:
                if voice_config.tts_provider == "azure":
                    tts_provider = azure_tts_cls(config=voice_config.azure)
                    logger.debug(f"TTS 提供商已初始化: Azure ({voice_config.azure.tts_voice})")
                elif voice_config.tts_provider == "ai_tts":
                    tts_provider = ai_tts_cls(config=voice_config.ai_tts)
                    logger.debug(f"TTS 提供商已初始化: AI Hobbyist TTS ({voice_config.ai_tts.model_name})")
                elif voice_config.tts_provider == "siliconflow":
                    tts_provider = siliconflow_tts_cls(config=voice_config.siliconflow)
                    logger.debug(f"TTS 提供商已初始化: SiliconFlow ({voice_config.siliconflow.tts_model})")
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(f"TTS 提供商初始化失败: {e}")

            # 初始化 STT 提供商
            stt_provider = None
            try:
                if voice_config.stt_provider == "azure":
                    stt_provider = azure_stt_cls(config=voice_config.azure)
                    logger.debug(f"STT 提供商已初始化: Azure ({voice_config.azure.stt_language})")
                elif voice_config.stt_provider == "aliyun":
                    stt_provider = aliyun_stt_cls(config=voice_config.aliyun)
                    logger.debug("STT 提供商已初始化: Aliyun")
                elif voice_config.stt_provider == "siliconflow":
                    stt_provider = siliconflow_stt_cls(config=voice_config.siliconflow)
                    logger.debug(f"STT 提供商已初始化: SiliconFlow ({voice_config.siliconflow.stt_model})")
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(f"STT 提供商初始化失败: {e}")

            # 创建语音管理器
            self.voice_manager = voice_manager_cls(
                bot=self.client,
                config=voice_config,
                tts_provider=tts_provider,
                stt_provider=stt_provider
            )

            if stt_provider:
                self.voice_manager.set_stt_callback(self._handle_stt_result)

            # 启动语音管理器
            await self.voice_manager.start()
            logger.info("语音功能已启动")

        except ImportError as e:
            logger.warning(f"导入语音模块失败，跳过语音功能: {e}")
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f"初始化语音功能失败: {e}")

    async def _handle_stt_result(self, member: discord.Member, text: str) -> None:
        """将语音识别结果转发到 MaiBot Core"""
        timestamp = time.time()

        # 构造用户信息
        user_info = UserInfo(
            platform=global_config.maibot_server.platform_name,
            user_id=str(member.id),
            user_nickname=member.display_name,
            user_cardname=getattr(member, "nick", None),
        )

        # 尝试定位所属频道
        voice_state = getattr(member, "voice", None)
        channel = getattr(voice_state, "channel", None)
        if channel is None and self.voice_manager and self.voice_manager.voice_client:
            channel = getattr(self.voice_manager.voice_client, "channel", None)

        group_info = None
        if channel and getattr(channel, "guild", None):
            guild_name = channel.guild.name
            group_info = GroupInfo(
                platform=global_config.maibot_server.platform_name,
                group_id=str(channel.id),
                group_name=f"{channel.name} (Voice) @ {guild_name}",
            )

        format_info = FormatInfo(
            content_format=["text"],
            accept_format=["text", "image", "emoji", "reply", "voice", "command", "file", "video"],
        )

        message_info = BaseMessageInfo(
            platform=global_config.maibot_server.platform_name,
            message_id=f"voice-{member.id}-{int(timestamp * 1000)}",
            time=timestamp,
            user_info=user_info,
            group_info=group_info,
            format_info=format_info,
        )

        message = MessageBase(
            message_info=message_info,
            message_segment=Seg(type="text", data=text),
            raw_message=text,
        )

        try:
            await router.send_message(message)
            logger.info(
                "已转发语音识别结果到 MaiCore: user=%s, channel=%s",
                member.id,
                getattr(channel, "id", None),
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.error(f"发送语音识别结果到 MaiCore 失败: {exc}")

    async def _on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
        ):
        """语音状态更新事件处理器"""
        if self.voice_manager:
            await self.voice_manager.on_voice_state_update(member, before, after)

    async def _on_error(self, event: str, *args, **kwargs):
        """Discord 客户端错误事件处理器
        
        Args:
            event: 发生错误的事件名称
            *args: 事件参数
            **kwargs: 事件关键字参数
        """
        logger.error(f"Discord 事件 {event} 发生错误: {args}, {kwargs}")

    async def _on_disconnect(self):
        """Discord 客户端断开连接事件处理器"""
        self.is_connected = False
        logger.warning("Discord 客户端连接断开")

    async def _on_resume(self):
        """Discord 客户端重新连接事件处理器"""
        self.is_connected = True
        logger.info("Discord 客户端连接已恢复")

    async def _on_message(self, message: discord.Message):
        """Discord 消息事件处理器
        
        处理接收到的 Discord 消息，进行基本过滤后放入消息队列
        
        Args:
            message: Discord 消息对象
        """
        try:
            # 详细的消息来源信息
            channel_info = (f"频道: {message.channel.name}"
                           if hasattr(message.channel, 'name') else "私信频道")
            guild_info = f"服务器: {message.guild.name}" if message.guild else "私信"

            logger.debug("收到消息事件:")
            logger.debug(f"  消息ID: {message.id}")
            logger.debug(f"  作者: {message.author.display_name} (ID: {message.author.id})")
            logger.debug(f"  内容: '{message.content}'")
            logger.debug(f"  {channel_info} (ID: {message.channel.id})")
            logger.debug(f"  {guild_info} (ID: {message.guild.id if message.guild else 'N/A'})")
            logger.debug(f"  消息类型: {type(message.channel).__name__}")

            # 忽略机器人自己发送的消息
            if message.author == self.client.user:
                logger.debug("忽略机器人自己发送的消息")
                return

            # 检查黑白名单
            guild_id = message.guild.id if message.guild else None
            channel_id = message.channel.id

            # 检查是否为子区消息
            is_thread_message = (
                hasattr(message.channel, 'parent')
                and message.channel.parent is not None
            )
            thread_id = None

            if is_thread_message:
                thread_id = message.channel.id  # 子区ID
                # 对于子区消息，如果继承父频道权限，则使用父频道ID进行权限检查
                if global_config.chat.inherit_channel_permissions:
                    channel_id = message.channel.parent.id if message.channel.parent else channel_id
                    logger.debug(f"子区消息继承父频道权限: 子区ID={thread_id}, 父频道ID={channel_id}")
                else:
                    logger.debug(f"子区消息使用独立权限: 子区ID={thread_id}")

            logger.debug(
                f"权限检查: 用户ID={message.author.id}, 服务器ID={guild_id}, "
                f"频道ID={channel_id}, 子区ID={thread_id}, 是否子区={is_thread_message}"
            )

            if not is_user_allowed(
                global_config,
                message.author.id,
                guild_id,
                channel_id,
                thread_id,
                is_thread_message,
            ):
                if is_thread_message:
                    logger.warning(f"用户 {message.author.id} 或子区 {thread_id} 不在允许列表中，忽略消息")
                else:
                    logger.warning(f"用户 {message.author.id} 或频道 {channel_id} 不在允许列表中，忽略消息")
                return

            # 将消息放入队列等待处理
            await self.message_queue.put(message)
            logger.debug(f"成功将 Discord 消息放入队列: {message.id}, 队列大小: {self.message_queue.qsize()}")

        except (AttributeError, TypeError, RuntimeError) as e:
            logger.error(f"处理 Discord 消息时发生错误: {e}")
            logger.error(f"错误详情: {traceback.format_exc()}")

    async def _reset_client(self):
        """
        重置客户端连接
        """
        # 关闭现有连接
        if self.client:
            try:
                if not self.client.is_closed():
                    await self.client.close()
                    logger.debug("旧客户端已关闭")
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(f"关闭旧客户端时出错: {e}")


        # 重新创建客户端
        self._setup_client()

        # 标记为未连接
        self.is_connected = False

        logger.info("Discord客户端已重置")

    async def start(self):
        """启动 Discord 客户端
        
        Raises:
            Exception: 当启动失败时抛出异常
        """
        # 获取重试配置
        retry_config = global_config.discord.retry
        retry_delay = retry_config.get('retry_delay', 5)

        logger.info(f"正在启动 Discord 客户端... (重试间隔: {retry_delay}s)")

        last_error = None
        attempt = 0
        while True:
            try:
                if attempt > 0:
                    logger.info(f"第 {attempt} 次重试启动 Discord 客户端...")
                    # 等待重试间隔
                    await asyncio.sleep(retry_delay)

                    # 重置客户端（避免连接状态问题）
                    await self._reset_client()

                # 直接启动客户端，让background_tasks处理连接监控
                logger.debug("开始尝试连接到Discord...")
                await self.client.start(global_config.discord.token)

                # 如果执行到这里，说明连接断开了
                logger.warning("Discord 客户端连接意外断开")
                last_error = None
                break  # 正常断开不需要重试

            except (discord.LoginFailure, discord.HTTPException) as e:
                last_error = str(e)
                logger.warning(f"第 {attempt + 1} 次尝试失败: {last_error}")

                # 检查是否是Token错误
                error_text = last_error.lower()
                if any(keyword in error_text for keyword in ("login", "token", "unauthorized")):
                    logger.error("Token 相关错误，请检查 Discord Bot Token 是否正确")
                    raise

            except (ConnectionError, TimeoutError, OSError) as e:
                last_error = str(e)
                logger.warning(f"第 {attempt + 1} 次尝试失败: {last_error}")

                # 记录详细错误信息
                if "信号灯超时" in str(e) or "timeout" in str(e).lower():
                    logger.warning("检测到网络超时，可能是网络连接问题或DNS拦截")
                elif "ssl" in str(e).lower():
                    logger.warning("检测到SSL错误，可能是证书问题或网络拦截")
                elif "name resolution" in str(e).lower() or "dns" in str(e).lower():
                    logger.warning("检测到DNS解析问题，可能是网络拦截或DNS污染")

                attempt += 1
                continue

            except Exception as e:  # pylint: disable=broad-except
                last_error = str(e)
                logger.error(f"第 {attempt + 1} 次尝试时发生未知错误: {last_error}")
                attempt += 1
                continue

            attempt += 1

        logger.info("Discord 客户端已停止运行")
        return

    async def stop(self):
        """停止 Discord 客户端"""
        self.is_shutting_down = True

        # 停止语音管理器
        if self.voice_manager:
            await self.voice_manager.close()
            logger.info("语音管理器已关闭")

        # 关闭Discord客户端
        if self.client and not self.client.is_closed():
            await self.client.close()
            logger.info("Discord 客户端已关闭")

    async def force_reconnect(self):
        """强制重连Discord客户端
        
        重连后会自动重新注册所有事件处理器。
        """
        if self.is_shutting_down:
            logger.debug("系统正在关闭，跳过重连")
            return

        # 防止重复重连
        if self.is_reconnecting:
            logger.debug("已有重连任务正在进行，跳过此次重连请求")
            return

        logger.info("强制重连Discord客户端...")
        self.is_reconnecting = True

        try:
            # 标记为未连接
            self.is_connected = False

            # 取消之前的重连任务（如果存在）
            if self._reconnect_task and not self._reconnect_task.done():
                logger.debug("取消之前的重连任务")
                self._reconnect_task.cancel()
                try:
                    await self._reconnect_task
                except asyncio.CancelledError:
                    pass

            # 关闭现有连接（设置较短超时，避免卡住）
            if self.client and not self.client.is_closed():
                try:
                    await asyncio.wait_for(self.client.close(), timeout=3.0)
                    logger.info("Discord客户端连接已断开")
                except asyncio.TimeoutError:
                    logger.warning("关闭Discord客户端超时，强制继续")

            # 短暂等待确保连接完全关闭
            await asyncio.sleep(0.5)

            # 重新创建客户端
            await self._reset_client()
            logger.info("Discord客户端已重置，启动重连任务...")

            # 启动新的连接（异步进行，不阻塞监控任务）
            self._reconnect_task = asyncio.create_task(self._reconnect_client())
            logger.debug(f"重连任务已创建: {self._reconnect_task}")

        except Exception as e:  # pylint: disable=broad-except
            logger.error(f"强制重连时发生错误: {e}")
            self.is_connected = False
            self.is_reconnecting = False
            logger.debug("force_reconnect异常，已重置is_reconnecting=False")

    async def _reconnect_client(self):
        """异步重连客户端"""
        try:
            # 获取重试配置
            retry_config = global_config.discord.retry
            retry_delay = retry_config.get('retry_delay', 5)

            attempt = 0
            while not self.is_shutting_down:
                try:
                    if attempt > 0:
                        logger.info(f"第 {attempt} 次重连尝试...")
                        await asyncio.sleep(retry_delay)
                        # 重要: 每次重连失败后必须重新创建client对象
                        # Discord client只能start一次！
                        await self._reset_client()

                    logger.debug("开始连接到Discord...")
                    await self.client.start(global_config.discord.token)

                    # 如果执行到这里，说明连接成功后又断开了
                    logger.info("Discord连接已断开，准备重试")
                    self.is_connected = False
                    attempt += 1

                except (discord.LoginFailure, discord.HTTPException) as e:
                    logger.error(f"重连过程中出现认证错误，停止重连: {e}")
                    return  # finally块会自动重置is_reconnecting

                except asyncio.CancelledError:
                    logger.info("重连任务被取消")
                    raise  # finally块会自动重置is_reconnecting

                except Exception as e:  # pylint: disable=broad-except
                    logger.warning(f"第 {attempt + 1} 次重连失败: {e}")
                    attempt += 1
                    continue

            if not self.is_shutting_down:
                logger.warning("重连循环结束，未能成功重连")

        except asyncio.CancelledError:
            logger.info("重连任务被取消")
            raise
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f"重连过程中发生错误: {e}")
        finally:
            # 确保重置重连标志
            self.is_reconnecting = False
            logger.debug("重连任务结束，is_reconnecting=False")

    async def get_channel(self, channel_id: int) -> discord.abc.Messageable | None:
        """获取频道对象
        
        Args:
            channel_id: 频道 ID
            
        Returns:
            discord.abc.Messageable | None: 频道对象，获取失败时返回 None
        """
        if not self.client:
            return None
        return self.client.get_channel(channel_id)

    async def get_user(self, user_id: int) -> discord.User | None:
        """获取用户对象
        
        Args:
            user_id: 用户 ID
            
        Returns:
            discord.User | None: 用户对象，获取失败时返回 None
        """
        if not self.client:
            return None
        return self.client.get_user(user_id)


# 创建全局客户端实例
discord_client: DiscordClientManager = DiscordClientManager()
