"""模块名称：Discord 消息处理器
主要功能：处理来自 Discord 的消息并转换为 MaiBot 标准格式
"""

import base64
import re
import traceback
from typing import List
import discord
from maim_message import BaseMessageInfo, UserInfo, GroupInfo, FormatInfo, MessageBase, Seg
from ..logger import logger
from ..config import global_config


class DiscordMessageHandler:
    """Discord 消息处理器
    
    负责将 Discord 消息转换为 MaiBot 标准格式
    
    Attributes:
        router: MaiBot 消息路由器（在 main.py 中设置）
    """
    
    def __init__(self):
        """初始化消息处理器"""
        self.router = None
    
    async def handle_discord_message(self, message: discord.Message):
        """处理 Discord 消息
        
        Args:
            message: Discord 消息对象
        """
        try:
            logger.debug("开始处理 Discord 消息转换:")
            logger.debug(f"  原始消息ID: {message.id}")
            logger.debug(f"  来源频道: {message.channel.name if hasattr(message.channel, 'name') else 'DM'} (ID: {message.channel.id})")
            logger.debug(f"  来源服务器: {message.guild.name if message.guild else '私信'} (ID: {message.guild.id if message.guild else 'N/A'})")
            
            # 转换消息格式
            maim_message = await self._convert_discord_to_maim(message)
            if not maim_message:
                logger.warning("消息转换失败，跳过该消息")
                return
                
            logger.debug("消息转换成功，准备发送到 MaiBot Core")
            logger.debug(f"  转换后平台: {maim_message.message_info.platform}")
            logger.debug(f"  转换后消息ID: {maim_message.message_info.message_id}")
            if maim_message.message_info.group_info:
                logger.debug(f"  转换后群组: {maim_message.message_info.group_info.group_name} (ID: {maim_message.message_info.group_info.group_id})")
            else:
                logger.debug("  转换后群组: 私聊")
                
            # 发送到 MaiBot Core
            if self.router:
                await self.router.send_message(maim_message)
                logger.debug(f"已成功转发 Discord 消息到 MaiBot Core: {message.id}")
            else:
                logger.error("MaiBot 路由器未设置，无法转发消息")
                
        except Exception as e:
            logger.error(f"处理 Discord 消息时发生错误: {e}")
            logger.error(f"错误详情: {traceback.format_exc()}")
    
    async def _convert_discord_to_maim(self, message: discord.Message) -> MessageBase | None:
        """将 Discord 消息转换为 MaiBot 格式
        
        Args:
            message: Discord 消息对象
            
        Returns:
            MessageBase | None: 转换后的 MaiBot 消息，转换失败时返回 None
        """
        try:
            logger.debug("开始构造 MaiBot 消息对象")
            
            # 构造用户信息
            # 获取各种用户名称信息
            username = message.author.name  # Discord用户名（如: john_doe）
            display_name = message.author.display_name  # 显示名称（全局昵称或用户名）
            server_nickname = getattr(message.author, 'nick', None) if hasattr(message.author, 'nick') else None  # 服务器昵称
            global_name = getattr(message.author, 'global_name', None) if hasattr(message.author, 'global_name') else None  # 全局显示名称
            
            user_info = UserInfo(
                platform=global_config.maibot_server.platform_name,
                user_id=str(message.author.id),
                user_nickname=display_name,  # 主要显示名称
                user_cardname=server_nickname  # 服务器内的昵称
            )
            
            # 详细记录用户信息
            logger.debug("用户信息详情:")
            logger.debug(f"  用户ID: {user_info.user_id}")
            logger.debug(f"  Discord用户名: {username}")
            logger.debug(f"  显示名称: {display_name}")
            if global_name:
                logger.debug(f"  全局昵称: {global_name}")
            if server_nickname:
                logger.debug(f"  服务器昵称: {server_nickname}")
            logger.debug(f"  是否为机器人: {message.author.bot}")
            
            # 构造群组信息（如果是服务器消息）
            group_info = None
            if message.guild:
                # 使用频道作为群组，这样可以精确定位回复位置
                # 格式: 频道名称 @ 服务器名称
                channel_name = message.channel.name if hasattr(message.channel, 'name') else f"频道{message.channel.id}"
                group_name = f"{channel_name} @ {message.guild.name}"
                
                group_info = GroupInfo(
                    platform=global_config.maibot_server.platform_name,
                    group_id=str(message.channel.id),  # 使用频道ID作为群组ID
                    group_name=group_name
                )
                logger.debug(f"群组信息 (频道): {group_info.group_name} (ID: {group_info.group_id})")
                logger.debug(f"服务器信息: {message.guild.name} (ID: {message.guild.id})")
            else:
                logger.debug("私聊消息，无群组信息")
            
            # 处理消息内容并构造消息段列表
            message_segments = []
            content_formats = []
            
            # 处理@提及
            mentions_info = await self._process_mentions(message)
            if mentions_info:
                message_segments.append(Seg(type="mention", data=mentions_info))
                if "mention" not in content_formats:
                    content_formats.append("mention")
                logger.debug(f"处理@提及: {len(mentions_info.get('users', []))}个用户, {len(mentions_info.get('roles', []))}个角色")
            
            # 处理文本内容（包含emoji检测和提及处理）
            if message.content:
                processed_content = await self._process_text_with_emojis(message.content, message)
                message_segments.extend(processed_content)
                
                # 更新content_formats
                for seg in processed_content:
                    if seg.type not in content_formats:
                        content_formats.append(seg.type)
            
            # 处理附件（图片等）
            for attachment in message.attachments:
                logger.debug(f"处理附件: {attachment.filename}, 类型: {attachment.content_type}")
                if attachment.content_type and attachment.content_type.startswith('image/'):
                    try:
                        # 下载图片并转换为 base64
                        image_data = await attachment.read()
                        image_base64 = base64.b64encode(image_data).decode('utf-8')
                        message_segments.append(Seg(type="image", data=image_base64))
                        content_formats.append("image")
                        logger.debug(f"处理图片附件: {attachment.filename}")
                    except Exception as e:
                        logger.error(f"处理图片附件失败: {e}")
                else:
                    logger.debug(f"跳过非图片附件: {attachment.filename} ({attachment.content_type})")
            
            # 处理Discord stickers（贴纸）
            for sticker in message.stickers:
                try:
                    logger.debug(f"发现Discord贴纸: {sticker.name} (ID: {sticker.id})")
                    # Discord贴纸可以通过URL获取图片
                    if hasattr(sticker, 'url'):
                        # 这里可以下载贴纸图片并转换为base64
                        # 暂时记录为文本，包含贴纸信息
                        sticker_text = f"[贴纸: {sticker.name}]"
                        if not message.content:
                            message_segments.append(Seg(type="text", data=sticker_text))
                            content_formats.append("text")
                        else:
                            # 如果已有文本内容，将贴纸信息追加
                            if message_segments and message_segments[-1].type == "text":
                                message_segments[-1].data += f" {sticker_text}"
                        logger.debug(f"处理Discord贴纸: {sticker.name}")
                except Exception as e:
                    logger.error(f"处理Discord贴纸失败: {e}")
            
            # 处理Discord reactions（表情反应）
            if message.reactions:
                reaction_text_parts = []
                for reaction in message.reactions:
                    emoji_str = str(reaction.emoji)
                    count = reaction.count
                    reaction_text_parts.append(f"{emoji_str}×{count}")
                
                if reaction_text_parts:
                    reaction_text = f"[表情反应: {', '.join(reaction_text_parts)}]"
                    # 如果已有文本内容，追加反应信息
                    if message_segments and message_segments[-1].type == "text":
                        message_segments[-1].data += f" {reaction_text}"
                    else:
                        message_segments.append(Seg(type="text", data=reaction_text))
                    logger.debug(f"处理Discord表情反应: {len(message.reactions)}个反应")
            
            # 处理回复消息
            if message.reference and message.reference.message_id:
                
                reply_message_id = str(message.reference.message_id)
                
                # 获取回复上下文用于显示（可选）
                reply_context = await self._get_reply_context(message)
                if reply_context:
                    # 在消息开头插入回复上下文文本
                    message_segments.insert(0, Seg(type="text", data=reply_context))
                
                message_segments.append(Seg(type="reply", data=reply_message_id))
                if "reply" not in content_formats:
                    content_formats.append("reply")
                logger.debug(f"处理回复消息: {reply_message_id}")
            
            # 如果没有任何内容，跳过该消息
            if not message_segments:
                logger.debug("消息没有可处理的内容，跳过")
                return None
            
            # 构造格式信息
            format_info = FormatInfo(
                content_format=content_formats if content_formats else ["text"],
                accept_format=["text", "image", "emoji", "reply", "voice", "command", "voiceurl", "music", "videourl", "file"]
            )
            # 部分格式现在无用
            
            # 构造消息元数据
            message_info = BaseMessageInfo(
                platform=global_config.maibot_server.platform_name,
                message_id=str(message.id),
                time=message.created_at.timestamp(),
                user_info=user_info,
                group_info=group_info,
                format_info=format_info
            )
            
            # 构造完整消息段
            if len(message_segments) == 1:
                message_segment = message_segments[0]
            else:
                message_segment = Seg(type="seglist", data=message_segments)
            
            return MessageBase(
                message_info=message_info,
                message_segment=message_segment,
                raw_message=message.content or ""
            )
            
        except Exception as e:
            logger.error(f"转换 Discord 消息时发生错误: {e}")
            return None
    
    async def _process_mentions(self, message: discord.Message) -> dict | None:
        """处理消息中的@提及信息
        
        Args:
            message: Discord消息对象
            
        Returns:
            dict | None: 包含提及信息的字典，没有提及时返回None
        """
        mentions_data = {}
        
        # 处理用户提及
        if message.mentions:
            users = []
            for user in message.mentions:
                # 获取用户的各种名称信息
                username = user.name  # Discord用户名
                display_name = user.display_name  # 显示名称
                global_name = getattr(user, 'global_name', None) if hasattr(user, 'global_name') else None  # 全局昵称
                server_nick = getattr(user, 'nick', None) if hasattr(user, 'nick') else None  # 服务器昵称
                
                user_data = {
                    "user_id": str(user.id),
                    "username": username,  # Discord原始用户名
                    "display_name": display_name,  # 当前显示名称
                    "global_name": global_name,  # 全局昵称（如果有）
                    "server_nickname": server_nick,  # 服务器内昵称（如果有）
                    "is_bot": user.bot,
                    "discriminator": getattr(user, 'discriminator', None)  # 用户标识符（旧版Discord）
                }
                users.append(user_data)
                logger.debug(f"提及用户详情: {username} (ID: {user.id}, 显示名: {display_name})")
            mentions_data["users"] = users
            logger.debug(f"检测到用户提及: {len(users)}个用户")
        
        # 处理角色提及
        if message.role_mentions:
            roles = []
            for role in message.role_mentions:
                role_data = {
                    "role_id": str(role.id),
                    "role_name": role.name,
                    "color": str(role.color),
                    "mentionable": role.mentionable
                }
                roles.append(role_data)
            mentions_data["roles"] = roles
            logger.debug(f"检测到角色提及: {len(roles)}个角色")
        
        # 处理频道提及
        if hasattr(message, 'channel_mentions') and message.channel_mentions:
            channels = []
            for channel in message.channel_mentions:
                channel_data = {
                    "channel_id": str(channel.id),
                    "channel_name": channel.name,
                    "channel_type": str(channel.type)
                }
                channels.append(channel_data)
            mentions_data["channels"] = channels
            logger.debug(f"检测到频道提及: {len(channels)}个频道")
        
        # 检查是否提及了所有人
        if "@everyone" in message.content or "@here" in message.content:
            mentions_data["everyone"] = "@everyone" in message.content
            mentions_data["here"] = "@here" in message.content
            logger.debug(f"检测到全体提及: everyone={mentions_data.get('everyone', False)}, here={mentions_data.get('here', False)}")
        
        return mentions_data if mentions_data else None

    async def _process_text_with_emojis(self, text: str, message: discord.Message = None) -> List[Seg]:
        """处理包含emoji和提及的文本内容
        
        Args:
            text: 原始文本内容
            message: Discord消息对象（用于获取提及信息）
            
        Returns:
            List[Seg]: 处理后的消息段列表
        """
        # 先处理提及，将<@user_id>替换为用户名
        processed_text = text
        if message and message.mentions:
            for user in message.mentions:
                # 替换<@!user_id>和<@user_id>格式
                user_mention_patterns = [f"<@!{user.id}>", f"<@{user.id}>"]
                for pattern in user_mention_patterns:
                    if pattern in processed_text:
                        # 优先使用服务器昵称，然后是全局昵称，最后是显示名称
                        server_nick = getattr(user, 'nick', None) if hasattr(user, 'nick') else None
                        global_name = getattr(user, 'global_name', None) if hasattr(user, 'global_name') else None
                        display_name = server_nick or global_name or user.display_name
                        
                        processed_text = processed_text.replace(pattern, f"@{display_name}")
                        logger.debug(f"替换用户提及: {pattern} -> @{display_name} (用户名: {user.name})")
        
        # 处理角色提及
        if message and message.role_mentions:
            for role in message.role_mentions:
                role_pattern = f"<@&{role.id}>"
                if role_pattern in processed_text:
                    processed_text = processed_text.replace(role_pattern, f"@{role.name}")
                    logger.debug(f"替换角色提及: {role_pattern} -> @{role.name}")
        
        # 处理频道提及
        if message and hasattr(message, 'channel_mentions'):
            for channel in message.channel_mentions:
                channel_pattern = f"<#{channel.id}>"
                if channel_pattern in processed_text:
                    processed_text = processed_text.replace(channel_pattern, f"#{channel.name}")
                    logger.debug(f"替换频道提及: {channel_pattern} -> #{channel.name}")
        
        # 继续处理emoji（使用处理后的文本）
        return await self._process_emoji_text(processed_text)
    
    async def _process_emoji_text(self, text: str) -> List[Seg]:
        # Unicode emoji正则表达式
        unicode_emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"
            "\U0001F300-\U0001F5FF"
            "\U0001F680-\U0001F6FF"
            "\U0001F1E0-\U0001F1FF"
            "\U00002702-\U000027B0"
            "\U000024C2-\U0001F251" 
            "]+", 
            flags=re.UNICODE
        )
        
        # Discord自定义emoji正则表达式 (<:name:id> 或 <a:name:id>)
        discord_custom_emoji_pattern = re.compile(r'<a?:(\w+):(\d+)>')
        
        # 先检测是否包含emoji
        has_unicode_emoji = bool(unicode_emoji_pattern.search(text))
        has_custom_emoji = bool(discord_custom_emoji_pattern.search(text))
        
        if not has_unicode_emoji and not has_custom_emoji:
            # 没有emoji，直接返回文本段
            return [Seg(type="text", data=text)]
        
        logger.debug(f"检测到emoji内容: Unicode={has_unicode_emoji}, Custom={has_custom_emoji}")
        
        # 如果包含emoji，需要分段处理
        segments = []
        current_pos = 0
        
        # 处理Discord自定义emoji
        for match in discord_custom_emoji_pattern.finditer(text):
            # 添加emoji前的文本
            if match.start() > current_pos:
                before_text = text[current_pos:match.start()]
                if before_text.strip():
                    segments.append(Seg(type="text", data=before_text))
            
            # 添加自定义emoji信息（作为文本，包含名称）
            emoji_name = match.group(1)
            emoji_id = match.group(2)
            is_animated = text[match.start():match.end()].startswith('<a:')
            
            emoji_text = f"[{emoji_name}]"
            if is_animated:
                emoji_text = f"[动画:{emoji_name}]"
                
            segments.append(Seg(type="text", data=emoji_text))
            logger.debug(f"处理Discord自定义emoji: {emoji_name} (ID: {emoji_id})")
            
            current_pos = match.end()
        
        # 添加剩余文本
        if current_pos < len(text):
            remaining_text = text[current_pos:]
            if remaining_text.strip():
                segments.append(Seg(type="text", data=remaining_text))
        
        # 如果只有Unicode emoji而没有自定义emoji，直接返回文本
        if has_unicode_emoji and not has_custom_emoji and not segments:
            segments.append(Seg(type="text", data=text))
        
        return segments if segments else [Seg(type="text", data=text)]
    
    async def _get_reply_context(self, message: discord.Message) -> str | None:
        """获取回复上下文信息，格式化为易读文本
        
        借鉴QQ适配器的处理方式，将回复信息格式化为类似
        "[回复<用户名:用户ID>：被回复内容]，说："的格式。
        
        Args:
            message: 包含回复引用的Discord消息对象
            
        Returns:
            str | None: 格式化后的回复上下文文本，获取失败时返回None
        """
        try:
            if not message.reference or not message.reference.message_id:
                return None
            
            # 尝试获取被回复的消息
            referenced_message = None
            try:
                if hasattr(message.reference, 'cached_message') and message.reference.cached_message:
                    referenced_message = message.reference.cached_message
                else:
                    referenced_message = await message.channel.fetch_message(message.reference.message_id)
            except (discord.NotFound, discord.Forbidden):
                logger.warning(f"无法获取被回复的消息: {message.reference.message_id}")
                return f"[回复消息{message.reference.message_id}]，说："
            except Exception as e:
                logger.warning(f"获取被回复消息时发生错误: {e}")
                return f"[回复消息{message.reference.message_id}]，说："
            
            if not referenced_message:
                return f"[回复消息{message.reference.message_id}]，说："
            
            # 构建回复上下文
            author_name = referenced_message.author.display_name
            author_id = referenced_message.author.id
            is_bot = referenced_message.author.bot
            content = referenced_message.content or "[无文本内容]"
            
            # 限制内容长度
            if len(content) > 100:
                content = content[:100] + "..."
            
            # 添加附件信息
            if referenced_message.attachments:
                attachment_count = len(referenced_message.attachments)
                content += f"[包含{attachment_count}个附件]"
            
            # 构建格式化文本
            user_type = "机器人" if is_bot else "用户"
            reply_text = f"[回复<{user_type}{author_name}:{author_id}>：{content}]，说："
            
            logger.debug(f"格式化回复上下文: {reply_text}")
            return reply_text
            
        except Exception as e:
            logger.error(f"处理回复上下文时发生错误: {e}")
            return f"[回复消息{message.reference.message_id}]，说："


# 创建全局消息处理器实例
message_handler = DiscordMessageHandler()