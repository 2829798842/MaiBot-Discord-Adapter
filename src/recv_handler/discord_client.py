"""模块名称：Discord 客户端管理器
主要功能：管理 Discord Bot 客户端连接和事件处理
"""

import discord
import asyncio
import traceback
from ..logger import logger
from ..config import global_config, is_user_allowed


class DiscordClientManager:
    """Discord 客户端管理器
    
    负责管理 Discord Bot 的连接、事件处理和消息队列
    
    Attributes:
        client (discord.Client | None): Discord 客户端实例
        message_queue (asyncio.Queue): 消息队列
        is_connected (bool): 连接状态
    """
    
    def __init__(self):
        """初始化 Discord 客户端管理器"""
        self.client = None
        self.message_queue = asyncio.Queue()
        self.is_connected = False
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
        
        logger.debug(f"Discord 权限意图: messages={intents.messages}, guilds={intents.guilds}, dm_messages={intents.dm_messages}, message_content={intents.message_content}")
        
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
        
        logger.debug("Discord 客户端初始化完成")
    
    async def _on_ready(self):
        """Discord 客户端就绪事件处理器
        
        当 Discord 客户端连接成功并准备就绪时调用
        """
        self.is_connected = True
        logger.debug(f"Discord 客户端已连接: {self.client.user}")
        logger.debug(f"Bot 已加入 {len(self.client.guilds)} 个服务器")
        
        # 显示加入的服务器信息
        for guild in self.client.guilds:
            logger.debug(f"服务器: {guild.name} (ID: {guild.id})")
            # 显示前几个频道
            text_channels = guild.text_channels[:3]  # 只显示前3个频道
            for channel in text_channels:
                logger.debug(f"  - 频道: {channel.name} (ID: {channel.id})")
        
        logger.debug("Discord 客户端准备就绪，等待消息事件...")
    
    async def _on_error(self, event: str, *args, **kwargs):
        """Discord 客户端错误事件处理器
        
        Args:
            event: 发生错误的事件名称
            *args: 事件参数
            **kwargs: 事件关键字参数
        """
        logger.error(f"Discord 事件 {event} 发生错误: {args}, {kwargs}")
    
    async def _on_message(self, message: discord.Message):
        """Discord 消息事件处理器
        
        处理接收到的 Discord 消息，进行基本过滤后放入消息队列
        
        Args:
            message: Discord 消息对象
        """
        try:
            # 详细的消息来源信息
            channel_info = f"频道: {message.channel.name}" if hasattr(message.channel, 'name') else "私信频道"
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
            
            logger.debug(f"权限检查: 用户ID={message.author.id}, 服务器ID={guild_id}, 频道ID={channel_id}")
            
            if not is_user_allowed(global_config, message.author.id, guild_id, channel_id):
                logger.warning(f"用户 {message.author.id} 或频道 {channel_id} 不在允许列表中，忽略消息")
                return
            
            # 将消息放入队列等待处理
            await self.message_queue.put(message)
            logger.debug(f"成功将 Discord 消息放入队列: {message.id}, 队列大小: {self.message_queue.qsize()}")
            
        except Exception as e:
            logger.error(f"处理 Discord 消息时发生错误: {e}")
            logger.error(f"错误详情: {traceback.format_exc()}")
    
    async def start(self):
        """启动 Discord 客户端
        
        Raises:
            Exception: 当启动失败时抛出异常
        """
        try:
            logger.info("正在启动 Discord 客户端...")
            await self.client.start(global_config.discord.token)
        except Exception as e:
            logger.error(f"启动 Discord 客户端失败: {e}")
            raise
    
    async def stop(self):
        """停止 Discord 客户端"""
        if self.client and not self.client.is_closed():
            await self.client.close()
            logger.info("Discord 客户端已关闭")
    
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
discord_client = DiscordClientManager()