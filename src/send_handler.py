"""模块名称：消息发送处理器
主要功能：处理来自 MaiBot Core 的消息并发送到 Discord
"""

import base64
import asyncio
import traceback
import discord
from maim_message import MessageBase, Seg
from .logger import logger
from .recv_handler.discord_client import discord_client


class DiscordSendHandler:
    """Discord 消息发送处理器
    
    负责将来自 MaiBot Core 的消息发送到 Discord
    
    Attributes:
        (无类属性)
    """
    
    def __init__(self):
        """初始化发送处理器"""
        pass
    
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
            maim_message: MaiBot 消息对象
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
            discord.abc.Messageable | None: 目标频道，找不到时返回 None
        """
        try:
            message_info = maim_message.message_info
            
            if message_info.group_info:
                # 服务器消息，group_id 现在是频道ID
                channel_id = int(message_info.group_info.group_id)
                channel = discord_client.client.get_channel(channel_id)
                
                if channel and isinstance(channel, discord.TextChannel):
                    # 检查是否有发送权限
                    if channel.permissions_for(channel.guild.me).send_messages:
                        logger.debug(f"找到目标频道: {channel.name} (ID: {channel.id}) 在服务器 {channel.guild.name}")
                        return channel
                    else:
                        logger.warning(f"没有权限在频道 {channel.name} (ID: {channel_id}) 发送消息")
                else:
                    logger.warning(f"找不到频道 {channel_id} 或频道类型不正确")
                    
                    # 备用方案：如果找不到具体频道，尝试从频道名称解析服务器并找到默认频道
                    # 这里可以添加更复杂的逻辑来处理频道不存在的情况
                    
            else:
                # 私聊消息
                user_id = int(message_info.user_info.user_id)
                user = discord_client.client.get_user(user_id)
                if user:
                    logger.debug(f"找到目标用户: {user.display_name} (ID: {user.id})")
                    return user.dm_channel or await user.create_dm()
                logger.warning(f"找不到用户 {user_id}")
            
        except Exception as e:
            logger.error(f"获取目标频道时发生错误: {e}")
        
        return None
    
    async def _process_message_content(self, message_segment: Seg) -> tuple[str | None, list[discord.File] | None]:
        """处理 MaiBot 消息内容
        
        Args:
            message_segment: 消息段
            
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
            maim_message: MaiBot 消息对象
            channel: 目标频道
            
        Returns:
            discord.Message | None: 被回复的消息对象
        """
        reply_message_id = self._extract_reply_message_id(maim_message.message_segment)
        if not reply_message_id:
            return None
        
        try:
            original_message = await channel.fetch_message(int(reply_message_id))
            return original_message
        except discord.NotFound:
            logger.warning(f"找不到被回复的消息: {reply_message_id}")
        except Exception as e:
            logger.error(f"获取回复消息时发生错误: {e}")
        
        return None
    
    def _extract_reply_message_id(self, message_segment: Seg) -> str | None:
        """提取回复消息的 ID
        
        Args:
            message_segment: 消息段
            
        Returns:
            str | None: 回复消息 ID，找不到时返回 None
        """
        def extract_from_segment(seg: Seg) -> str | None:
            if seg.type == "reply":
                return str(seg.data)
            elif seg.type == "seglist" and isinstance(seg.data, list):
                for sub_seg in seg.data:
                    result = extract_from_segment(sub_seg)
                    if result:
                        return result
            return None
        
        return extract_from_segment(message_segment)


# 创建全局发送处理器实例
send_handler = DiscordSendHandler()