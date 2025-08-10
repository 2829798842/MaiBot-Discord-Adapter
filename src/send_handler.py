"""模块名称：消息发送处理器
主要功能：处理来自MaiBot Core的消息并发送到Discord
"""

import asyncio
import base64
import traceback
import discord
from maim_message import MessageBase, Seg
from .logger import logger
from .recv_handler.discord_client import discord_client


class DiscordSendHandler:
    """Discord消息发送处理器
    
    负责将来自 MaiBot Core 的消息发送到 Discord
    
    Attributes:
        _channel_cache: 频道缓存字典，提高获取效率
        _user_cache: 用户缓存字典，提高获取效率
    """
    
    def __init__(self):
        """初始化Discord发送处理器"""
        self._channel_cache = {}  # 频道缓存
        self._user_cache = {}     # 用户缓存
        
    def _cache_channel(self, channel_id: int, channel: discord.abc.Messageable):
        """缓存频道对象
        
        Args:
            channel_id: 频道ID
            channel: Discord频道对象
        """
        self._channel_cache[channel_id] = channel
        
    def _cache_user(self, user_id: int, user: discord.User):
        """缓存用户对象
        
        Args:
            user_id: 用户ID
            user: Discord用户对象
        """
        self._user_cache[user_id] = user
    
    async def handle_message(self, message_dict: dict):
        """处理来自 MaiBot Core 的消息
        
        Args:
            message_dict: MaiBot 消息字典
        """
        try:
            # 将字典转换为 MessageBase 对象
            maim_message = MessageBase.from_dict(message_dict)
            
            # 转换并发送到 Discord
            await self._send_to_discord(maim_message)
            logger.debug(f"已转发 MaiBot 消息到 Discord: {maim_message.message_info.message_id}")
            
        except Exception as e:
            logger.error(f"处理 MaiBot 消息时发生错误: {e}")
    
    async def _send_to_discord(self, maim_message: MessageBase):
        """将 MaiBot 消息发送到 Discord
        
        Args:
            maim_message: MaiBot消息对象
        """
        try:
            logger.debug("开始发送消息到 Discord:")
            logger.debug(f"  原始消息ID: {maim_message.message_info.message_id}")
            if maim_message.message_info.group_info:
                logger.debug(f"  目标群组: {maim_message.message_info.group_info.group_name}")
                logger.debug(f"  目标群组ID: {maim_message.message_info.group_info.group_id}")
            else:
                logger.debug(f"  目标用户: {maim_message.message_info.user_info.user_nickname}")
                logger.debug(f"  目标用户ID: {maim_message.message_info.user_info.user_id}")
            
            # 确定目标频道
            target_channel = await self._get_target_channel(maim_message)
            if not target_channel:
                logger.warning("无法找到目标频道，消息发送失败")
                return
            
            # 处理消息内容
            content, files = await self._process_message_content(maim_message.message_segment)
            logger.debug(f"  消息内容: {content[:100] + '...' if content and len(content) > 100 else content}")
            logger.debug(f"  文件数量: {len(files) if files else 0}")
            
            # 处理回复消息
            reference = await self._get_reply_reference(maim_message, target_channel)
            if reference:
                logger.debug(f"  回复消息ID: {reference.id}")
            
            # 发送消息
            if content or files:
                sent_message = await target_channel.send(
                    content=content if content else None,
                    files=files if files else None,
                    reference=reference
                )
                logger.debug(f"消息发送成功: {sent_message.id} 到频道 {target_channel.name if hasattr(target_channel, 'name') else 'DM'}")
            else:
                logger.warning("消息内容为空，跳过发送")
            
        except Exception as e:
            logger.error(f"发送 Discord 消息时发生错误: {e}")
            logger.error(f"错误详情: {traceback.format_exc()}")
    
    async def _get_target_channel(self, maim_message: MessageBase) -> discord.abc.Messageable | None:
        """根据消息信息获取目标频道
        
        Args:
            maim_message: MaiBot 消息对象
            
        Returns:
            discord.abc.Messageable | None: 目标频道对象，找不到或无权限时返回None
        """
        try:
            message_info = maim_message.message_info
            
            if message_info.group_info:
                # 服务器消息，group_id 现在是频道ID
                channel_id = int(message_info.group_info.group_id)
                
                # 优先从缓存获取
                channel = self._channel_cache.get(channel_id)
                if not channel:
                    channel = discord_client.client.get_channel(channel_id)
                    if channel:
                        self._cache_channel(channel_id, channel)
                
                if channel and isinstance(channel, discord.TextChannel):
                    # 检查是否有发送权限
                    bot_permissions = channel.permissions_for(channel.guild.me)
                    if not bot_permissions.send_messages:
                        logger.warning(f"没有权限在频道 {channel.name} (ID: {channel_id}) 发送消息")
                        return None
                    
                    # 检查是否需要嵌入链接权限（用于发送文件）
                    if not bot_permissions.embed_links:
                        logger.warning(f"频道 {channel.name} 缺少嵌入链接权限，可能影响文件发送")
                    
                    logger.debug(f"找到目标频道: {channel.name} (ID: {channel.id}) 在服务器 {channel.guild.name}")
                    return channel
                else:
                    logger.warning(f"找不到频道 {channel_id} 或频道类型不正确")
                    
                    try:
                        channel = await discord_client.client.fetch_channel(channel_id)
                        if isinstance(channel, discord.TextChannel):
                            if channel.permissions_for(channel.guild.me).send_messages:
                                logger.debug(f"从API获取到频道: {channel.name} (ID: {channel.id})")
                                self._cache_channel(channel_id, channel)  # 缓存新获取的频道
                                return channel
                            else:
                                logger.warning(f"没有权限在频道 {channel.name} (ID: {channel_id}) 发送消息")
                        else:
                            logger.warning(f"频道 {channel_id} 不是文本频道")
                    except discord.NotFound:
                        logger.error(f"频道 {channel_id} 不存在")
                    except discord.Forbidden:
                        logger.error(f"没有权限访问频道 {channel_id}")
                    except discord.HTTPException as e:
                        logger.error(f"获取频道时发生错误: {e}")
                    
            else:
                # 私聊消息
                user_id = int(message_info.user_info.user_id)
                
                # 优先从缓存获取
                user = self._user_cache.get(user_id)
                if not user:
                    user = discord_client.client.get_user(user_id)
                    if user:
                        self._cache_user(user_id, user)
                if user:
                    logger.debug(f"找到目标用户: {user.display_name} (ID: {user.id})")
                    if user.dm_channel:
                        return user.dm_channel
                    else:
                        try:
                            return await user.create_dm()
                        except discord.HTTPException as e:
                            logger.error(f"创建DM频道失败: {e}")
                            return None
                else:
                    # 如果缓存中没有用户，尝试从API获取
                    try: 
                        user = await discord_client.client.fetch_user(user_id)
                        logger.debug(f"从API获取到用户: {user.display_name} (ID: {user.id})")
                        self._cache_user(user_id, user)  # 缓存新获取的用户
                        return await user.create_dm()
                    except discord.NotFound:
                        logger.warning(f"用户 {user_id} 不存在")
                    except discord.HTTPException as e:
                        logger.error(f"获取用户时发生错误: {e}")
                
                logger.warning(f"找不到用户 {user_id}")
            
        except Exception as e:
            logger.error(f"获取目标频道时发生错误: {e}")
        
        return None
    
    async def _process_message_content(self, message_segment: Seg) -> tuple[str | None, list[discord.File] | None]:
        """处理 MaiBot 消息内容
        
        Args:
            message_segment: MaiBot消息段对象
            
        Returns:
            tuple[str | None, list[discord.File] | None]: 文本内容和文件列表的元组
        """
        content_parts = []
        files = []
        
        def process_segment(seg: Seg):
            """递归处理消息段
            
            Args:
                seg: 要处理的消息段
            """
            if seg.type == "text":
                content_parts.append(str(seg.data))
            elif seg.type == "mention":
                # 处理@提及段
                mention_text = self._process_mention_segment(seg.data)
                if mention_text:
                    content_parts.append(mention_text)
            elif seg.type == "emoji":
                try:
                    # 尝试将emoji数据作为Unicode emoji处理
                    emoji_text = str(seg.data)
                    
                    # 检查是否是base64编码的图片数据
                    try:
                        # 如果是base64，解码并作为图片文件发送
                        if len(emoji_text) > 50 and not any(c in emoji_text for c in [' ', '\n', '\t']):
                            emoji_data = base64.b64decode(seg.data)
                            files.append(discord.File(
                                fp=asyncio.BytesIO(emoji_data),
                                filename="emoji.png"
                            ))
                            logger.debug("处理base64表情包文件")
                        else:
                            # 否则作为Unicode emoji文本处理
                            content_parts.append(emoji_text)
                            logger.debug(f"处理Unicode emoji文本: {emoji_text}")
                    except Exception:
                        # base64解码失败，作为文本处理
                        content_parts.append(emoji_text)
                        logger.debug(f"表情包数据解码失败，作为文本处理: {emoji_text[:50]}...")
                        
                except Exception as e:
                    logger.error(f"处理表情包时发生错误: {e}")
            elif seg.type == "image":
                try:
                    # 解码 base64 图片
                    image_data = base64.b64decode(seg.data)
                    files.append(discord.File(
                        fp=asyncio.BytesIO(image_data),
                        filename="image.png"
                    ))
                except Exception as e:
                    logger.error(f"处理图片时发生错误: {e}")
            elif seg.type == "voice":
                try:
                    # 解码 base64 语音
                    voice_data = base64.b64decode(seg.data)
                    files.append(discord.File(
                        fp=asyncio.BytesIO(voice_data),
                        filename="voice.wav"
                    ))
                except Exception as e:
                    logger.error(f"处理语音时发生错误: {e}")
            elif seg.type == "seglist" and isinstance(seg.data, list):
                for sub_seg in seg.data:
                    process_segment(sub_seg)
        
        process_segment(message_segment)
        
        content = "\n".join(content_parts) if content_parts else None
        return content, files if files else None
    
    async def _get_reply_reference(self, maim_message: MessageBase, channel: discord.abc.Messageable) -> discord.Message | None:
        """获取回复消息的引用
        
        Args:
            maim_message: MaiBot消息对象，可能包含回复信息
            channel: 目标频道，用于获取原始消息
            
        Returns:
            discord.Message | None: 被回复的Discord消息对象，找不到时返回None
        """
        reply_message_id = self._extract_reply_message_id(maim_message.message_segment)
        if not reply_message_id:
            return None
        
        try:
            # 验证消息ID格式
            message_id_int = int(reply_message_id)
            logger.debug(f"尝试获取被回复消息: {message_id_int}")
            
            # 优化：先尝试从缓存获取消息
            if hasattr(channel, 'get_partial_message'):
                # 对于文本频道，可以使用部分消息对象进行优化
                partial_message = channel.get_partial_message(message_id_int)
                try:
                    # 尝试通过部分消息获取完整信息
                    original_message = await partial_message.fetch()
                    logger.debug(f"成功获取被回复消息: {original_message.id} by {original_message.author.display_name}")
                    return original_message
                except discord.NotFound:
                    logger.warning(f"被回复的消息已被删除: {reply_message_id}")
                except discord.Forbidden:
                    logger.warning(f"没有权限访问被回复的消息: {reply_message_id}")
                    return None
            else:
                # 回退到常规获取方式
                original_message = await channel.fetch_message(message_id_int)
                logger.debug(f"成功获取被回复消息: {original_message.id} by {original_message.author.display_name}")
                return original_message
            
        except ValueError:
            logger.warning(f"回复消息ID格式无效: {reply_message_id}")
        except discord.HTTPException as e:
            logger.warning(f"Discord API错误: {e}")
        except Exception as e:
            logger.error(f"获取回复消息时发生未知错误: {e}")
        
        return None
    
    def _extract_reply_message_id(self, message_segment: Seg) -> str | None:
        """提取回复消息的ID
        
        从消息段中递归查找回复类型的段，并提取其中的消息ID。
        支持简单字符串格式和复杂字典格式的回复数据。
        
        Args:
            message_segment: MaiBot消息段对象
            
        Returns:
            str | None: 回复消息ID字符串，找不到时返回None
        """
        def extract_from_segment(seg: Seg) -> str | None:
            if seg.type == "reply":
                # 处理两种格式的回复数据
                if isinstance(seg.data, str):
                    # 简单格式：直接是消息ID字符串
                    return seg.data
                elif isinstance(seg.data, dict):
                    # 复杂格式：包含详细信息的字典
                    return seg.data.get("message_id")
                else:
                    # 尝试转换为字符串
                    try:
                        return str(seg.data)
                    except Exception:
                        logger.warning(f"无法解析回复数据: {seg.data}")
                        return None
            elif seg.type == "seglist" and isinstance(seg.data, list):
                for sub_seg in seg.data:
                    result = extract_from_segment(sub_seg)
                    if result:
                        return result
            return None
        
        return extract_from_segment(message_segment)
    
    def _process_mention_segment(self, mention_data: dict) -> str:
        """处理mention段，转换为Discord提及格式
        
        将MaiBot标准的提及信息转换为Discord原生的提及格式。
        支持用户提及、角色提及、@everyone等多种类型。
        
        Args:
            mention_data: 提及数据字典，包含users、roles、everyone等字段
            
        Returns:
            str: 转换后的Discord提及文本
        """
        mention_parts = []
        
        try:
            # 处理用户提及
            if "users" in mention_data:
                for user_mention in mention_data["users"]:
                    user_id = user_mention.get("user_id")
                    display_name = user_mention.get("display_name", user_mention.get("username", "未知用户"))
                    if user_id:
                        # 使用Discord的提及格式
                        mention_parts.append(f"<@{user_id}>")
                        logger.debug(f"转换用户提及: @{display_name} -> <@{user_id}>")
                    else:
                        # 降级为文本提及
                        mention_parts.append(f"@{display_name}")
            
            # 处理角色提及
            if "roles" in mention_data:
                for role_mention in mention_data["roles"]:
                    role_id = role_mention.get("role_id")
                    role_name = role_mention.get("name", "未知角色")
                    if role_id:
                        mention_parts.append(f"<@&{role_id}>")
                        logger.debug(f"转换角色提及: @{role_name} -> <@&{role_id}>")
                    else:
                        mention_parts.append(f"@{role_name}")
            
            # 处理@everyone
            if mention_data.get("everyone", False):
                mention_parts.append("@everyone")
                logger.debug("转换@everyone提及")
            
            return " ".join(mention_parts) if mention_parts else ""
            
        except Exception as e:
            logger.error(f"处理mention段时发生错误: {e}")
            return ""


# 创建全局发送处理器实例
send_handler = DiscordSendHandler()