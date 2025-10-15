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
from .emoji_mapping import format_reaction_for_ai


class DiscordMessageHandler:
    """Discord 消息处理器
    
    负责将 Discord 消息转换为 MaiBot 标准格式
    
    Attributes:
        router: MaiBot 消息路由器（在 main.py 中设置）
        send_handler: Discord 发送处理器引用，用于更新上下文映射
    """

    router: any
    send_handler: any

    def __init__(self):
        """初始化消息处理器"""
        self.router = None
        self.send_handler = None  # 将在 main.py 中设置

    async def handle_discord_message(self, message: discord.Message):
        """处理 Discord 消息
        
        Args:
            message: Discord 消息对象
        """
        try:
            logger.debug("开始处理 Discord 消息转换:")
            logger.debug(f"  原始消息ID: {message.id}")
            channel_name = message.channel.name if hasattr(message.channel, 'name') else 'DM'
            logger.debug(f"  来源频道: {channel_name} (ID: {message.channel.id})")
            guild_name = message.guild.name if message.guild else '私信'
            guild_id = message.guild.id if message.guild else 'N/A'
            logger.debug(f"  来源服务器: {guild_name} (ID: {guild_id})")

            # 转换消息格式
            maim_message = await self._convert_discord_to_maim(message)
            if not maim_message:
                logger.warning("消息转换失败，跳过该消息")
                return

            logger.debug("消息转换成功，准备发送到 MaiBot Core")
            logger.debug(f"  转换后平台: {maim_message.message_info.platform}")
            logger.debug(f"  转换后消息ID: {maim_message.message_info.message_id}")
            if maim_message.message_info.group_info:
                group_name = maim_message.message_info.group_info.group_name
                group_id = maim_message.message_info.group_info.group_id
                logger.debug(f"  转换后群组: {group_name} (ID: {group_id})")
            else:
                logger.debug("  转换后群组: 私聊")

            # 发送到 MaiBot Core
            if self.router:
                await self.router.send_message(maim_message)
                logger.debug(f"已成功转发 Discord 消息到 MaiBot Core: {message.id}")
                
                # 更新发送处理器的上下文映射
                if self.send_handler:
                    if (hasattr(message.channel, 'parent') and message.channel.parent is not None and
                        global_config.chat.inherit_channel_memory):
                        # 子区消息：更新上下文映射
                        parent_channel_id = str(message.channel.parent.id)
                        thread_id = str(message.channel.id)
                        self.send_handler.update_thread_context(parent_channel_id, thread_id)
                        logger.debug(f"更新子区上下文映射: 父频道{parent_channel_id} -> 子区{thread_id}")
                    elif (hasattr(message.channel, 'type') and 
                          message.channel.type == discord.ChannelType.text and
                          global_config.chat.inherit_channel_memory):
                        # 父频道消息：清除该频道的子区映射，确保回复发送到父频道
                        parent_channel_id = str(message.channel.id)
                        self.send_handler.clear_thread_context(parent_channel_id)
                        logger.debug(f"清除父频道的子区映射，确保回复发送到父频道: {parent_channel_id}")
            else:
                logger.error("MaiBot 路由器未设置，无法转发消息")

        except (AttributeError, ValueError, TypeError) as e:
            logger.error(f"处理 Discord 消息时发生错误: {e}")
            logger.error(f"错误详情: {traceback.format_exc()}")
        except Exception as e:
            logger.error(f"处理 Discord 消息时发生未知错误: {e}")
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
            username = message.author.name  # Discord用户名
            display_name = message.author.display_name  # 显示名称（全局昵称或用户名）
            # 服务器昵称
            server_nickname = (getattr(message.author, 'nick', None)
                             if hasattr(message.author, 'nick') else None)
            # 全局显示名称
            global_name = (getattr(message.author, 'global_name', None)
                         if hasattr(message.author, 'global_name') else None)

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
            # 初始化子区上下文变量
            thread_context_marker = False
            original_thread_id = None
            thread_name = None

            if message.guild:
                # 检查是否为Thread消息
                is_thread_message = hasattr(message.channel, 'parent') and message.channel.parent is not None

                if is_thread_message:
                    # Thread消息：根据配置决定是否继承父频道记忆
                    thread_name = message.channel.name
                    parent_channel_name = (message.channel.parent.name 
                                         if hasattr(message.channel.parent, 'name') 
                                         else f"频道{message.channel.parent.id}")

                    if global_config.chat.inherit_channel_memory:
                        # 继承父频道记忆：使用父频道ID作为群组ID，但在消息中保留子区信息
                        group_id = str(message.channel.parent.id)  # 使用父频道ID实现记忆共享
                        # 格式: 父频道名称 @ 服务器名称 (用于显示和记忆)
                        group_name = f"{parent_channel_name} @ {message.guild.name}"

                        # 在群组名称中添加子区上下文信息，帮助AI理解当前在哪个子区
                        actual_context = f"[当前子区: {thread_name}] {group_name}"
                        group_info = GroupInfo(
                            platform=global_config.maibot_server.platform_name,
                            group_id=group_id,  # 父频道ID - 用于记忆共享
                            group_name=actual_context  # 包含子区上下文的名称
                        )
                        logger.debug(f"子区继承父频道记忆: 子区={thread_name}, 使用父频道ID={group_id}, 但保留子区上下文")

                        # 重要：标记这是一个从子区发出的消息，用于回复时的路由
                        thread_context_marker = True
                        original_thread_id = str(message.channel.id)
                    else:
                        # 独立记忆：使用子区ID作为群组ID
                        group_id = str(message.channel.id)
                        # 格式: 子区名称 [父频道名称] @ 服务器名称
                        group_name = f"{thread_name} [{parent_channel_name}] @ {message.guild.name}"
                        group_info = GroupInfo(
                            platform=global_config.maibot_server.platform_name,
                            group_id=group_id,
                            group_name=group_name
                        )
                        logger.debug(f"子区使用独立记忆: 子区ID={group_id}")
                        thread_context_marker = False
                        original_thread_id = None

                    logger.debug(f"群组信息 (子区): {group_info.group_name} (ID: {group_info.group_id})")
                    logger.debug(f"父频道信息: {parent_channel_name} (ID: {message.channel.parent.id})")
                    logger.debug(f"实际子区信息: {thread_name} (ID: {message.channel.id})")
                    logger.debug(f"服务器信息: {message.guild.name} (ID: {message.guild.id})")
                else:
                    # 普通频道消息：使用频道作为群组
                    channel_name = (message.channel.name if hasattr(message.channel, 'name')
                                  else f"频道{message.channel.id}")
                    # 格式: 频道名称 @ 服务器名称
                    group_name = f"{channel_name} @ {message.guild.name}"

                    group_info = GroupInfo(
                        platform=global_config.maibot_server.platform_name,
                        group_id=str(message.channel.id),  # 使用频道ID作为群组ID
                        group_name=group_name
                    )
                    logger.debug(f"群组信息 (频道): {group_info.group_name} (ID: {group_info.group_id})")
                    logger.debug(f"服务器信息: {message.guild.name} (ID: {message.guild.id})")
                    thread_context_marker = False
                    original_thread_id = None
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
                logger.debug(
                    f"处理@提及: {len(mentions_info.get('users', []))}个用户, "
                    f"{len(mentions_info.get('roles', []))}个角色"
                )

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
                    except (discord.HTTPException, discord.NotFound, OSError) as e:
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
                except (AttributeError, TypeError) as e:
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

            # 如果是启用记忆继承的子区消息，在消息段中添加路由信息
            if thread_context_marker and original_thread_id:
                # 添加一个特殊的路由信息段，用于指导回复时的目标选择
                thread_routing_info = {
                    "original_thread_id": original_thread_id,
                    "thread_name": thread_name,
                    "parent_channel_id": str(message.channel.parent.id),
                    "inherit_memory": True
                }
                message_segments.append(Seg(type="thread_context", data=thread_routing_info))
                logger.debug(f"添加子区路由信息: {thread_routing_info}")

            # 如果没有任何内容，跳过该消息
            if not message_segments:
                logger.debug("消息没有可处理的内容，跳过")
                return None

            # 构造格式信息
            format_info = FormatInfo(
                content_format=content_formats if content_formats else ["text"],
                accept_format=[
                    "text", "image", "emoji", "reply", "voice", "command", 
                    "file", "video"
                ]
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

        except (AttributeError, ValueError, TypeError) as e:
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
                global_name = (getattr(user, 'global_name', None)
                             if hasattr(user, 'global_name') else None)
                server_nick = (getattr(user, 'nick', None)
                             if hasattr(user, 'nick') else None)
                # 全局或当前服务器名称

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
            logger.debug(
                f"检测到全体提及: everyone={mentions_data.get('everyone', False)}, "
                f"here={mentions_data.get('here', False)}"
            )

        return mentions_data if mentions_data else None

    async def _process_text_with_emojis(self, text: str,
                                       message: discord.Message = None) -> List[Seg]:
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
                if (hasattr(message.reference, 'cached_message') and
                    message.reference.cached_message):
                    referenced_message = message.reference.cached_message
                else:
                    message_id = message.reference.message_id
                    referenced_message = await message.channel.fetch_message(message_id)
            except (discord.NotFound, discord.Forbidden):
                logger.warning(f"无法获取被回复的消息: {message.reference.message_id}")
                return f"[回复消息{message.reference.message_id}]，说："
            except (discord.HTTPException, AttributeError) as e:
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

        except (AttributeError, TypeError, ValueError) as e:
            logger.error(f"处理回复上下文时发生错误: {e}")
            return f"[回复消息{message.reference.message_id}]，说："

    async def handle_reaction_event(self, event_type: str, payload: discord.RawReactionActionEvent):
        """处理reaction事件
        
        Args:
            event_type: 事件类型 ('reaction_add' 或 'reaction_remove')
            payload: Discord reaction事件数据
        """
        try:
            logger.debug(f"开始处理 {event_type} 事件:")
            logger.debug(f"  消息ID: {payload.message_id}")
            logger.debug(f"  用户ID: {payload.user_id}")
            logger.debug(f"  Emoji: {payload.emoji}")
            
            # 导入discord_client以获取频道和用户信息
            from .discord_client import discord_client
            client = discord_client.client
            if not client:
                logger.warning("Discord客户端未初始化，忽略reaction事件")
                return
            
            # 获取用户和成员信息
            user: discord.abc.User | None = None
            member: discord.Member | None = None

            if payload.member:
                member = payload.member
                user = payload.member
            else:
                # 优先从 guild 中获取成员
                guild = client.get_guild(payload.guild_id) if payload.guild_id else None
                if guild:
                    member = guild.get_member(payload.user_id)
                    if not member:
                        try:
                            member = await guild.fetch_member(payload.user_id)
                        except discord.NotFound:
                            logger.warning(f"公会 {guild.id} 中找不到成员 {payload.user_id}")
                        except discord.HTTPException as fetch_member_error:
                            logger.error(f"获取成员 {payload.user_id} 信息失败: {fetch_member_error}")

                if member:
                    user = member
                else:
                    # 退回到用户级别
                    try:
                        user = await client.fetch_user(payload.user_id)
                    except (discord.NotFound, discord.HTTPException) as fetch_user_error:
                        logger.error(f"获取用户 {payload.user_id} 信息失败: {fetch_user_error}")
                        return

            if not user:
                logger.error(f"无法获取reaction用户 {payload.user_id} 的信息")
                return

            user_id = user.id
            # Discord API 提供 display_name 属性
            user_display = getattr(user, "display_name", None) or user.name
            server_nickname = getattr(member, "nick", None) if member else None
            logger.debug(f"  用户: {user_display} (ID: {user_id})")
            
            # 获取频道信息
            channel = client.get_channel(payload.channel_id)
            if not channel:
                try:
                    channel = await client.fetch_channel(payload.channel_id)
                except (discord.NotFound, discord.Forbidden):
                    logger.warning(f"无法获取频道 {payload.channel_id}")
                    return
                except discord.HTTPException as fetch_channel_error:
                    logger.error(f"获取频道 {payload.channel_id} 时发生错误: {fetch_channel_error}")
                    return
            
            channel_name = channel.name if hasattr(channel, 'name') else 'DM'
            
            # 判断是否为子区
            is_thread = isinstance(channel, discord.Thread)
            
            # 获取服务器信息
            guild = client.get_guild(payload.guild_id) if payload.guild_id else None
            guild_name = guild.name if guild else "私信"
            
            logger.debug(f"  频道: {channel_name} (ID: {payload.channel_id})")
            logger.debug(f"  服务器: {guild_name}")
            logger.debug(f"  是否子区: {is_thread}")
            
            platform_name = global_config.maibot_server.platform_name

            # 构建用户信息
            user_info = UserInfo(
                platform=platform_name,
                user_id=str(user_id),
                user_nickname=user_display,
                user_cardname=server_nickname
            )

            # 构建群组信息
            group_info = None
            if guild:
                if is_thread:
                    parent_channel = getattr(channel, 'parent', None)
                    parent_name = getattr(parent_channel, 'name', f"频道{parent_channel.id}" if parent_channel else channel_name)
                    thread_name = getattr(channel, 'name', f"子区{channel.id}")

                    if global_config.chat.inherit_channel_memory:
                        group_id = str(parent_channel.id) if parent_channel else str(channel.id)
                        display_name = f"[Reaction子区: {thread_name}] {parent_name} @ {guild_name}"
                    else:
                        group_id = str(channel.id)
                        display_name = f"{thread_name} @ {guild_name}"

                    group_info = GroupInfo(
                        platform=platform_name,
                        group_id=group_id,
                        group_name=display_name
                    )
                else:
                    channel_display = getattr(channel, 'name', f"频道{channel.id}")
                    group_info = GroupInfo(
                        platform=platform_name,
                        group_id=str(channel.id),
                        group_name=f"{channel_display} @ {guild_name}"
                    )
            
            # 获取emoji信息
            emoji = payload.emoji
            if emoji.is_unicode_emoji():
                emoji_str = emoji.name  # Unicode emoji
                emoji_name = None
            else:
                # 自定义emoji
                emoji_str = f"<:{emoji.name}:{emoji.id}>"
                emoji_name = emoji.name
            
            logger.debug(f"  Emoji类型: {'Unicode' if emoji.is_unicode_emoji() else '自定义'}")
            logger.debug(f"  Emoji值: {emoji_str}")
            
            # 格式化reaction描述
            action_text = "添加了" if event_type == "reaction_add" else "移除了"
            description = format_reaction_for_ai(emoji_str, emoji_name, 1, user_display)
            # 调整描述文本
            description = description.replace("添加了", action_text)
            
            logger.debug(f"  格式化描述: {description}")
            
            # 构建notify类型消息段
            notify_data = {
                "type": "reaction",
                "action": "add" if event_type == "reaction_add" else "remove",
                "message_id": str(payload.message_id),
                "emoji": emoji_str,
                "description": description
            }
            
            notify_seg = Seg(type="notify", data=notify_data)
            
            # 构建 BaseMessageInfo
            base_message_info = BaseMessageInfo(
                platform=platform_name,
                user_info=user_info,
                group_info=group_info,
                format_info=FormatInfo()
            )
            
            # 构建完整的 MessageBase
            maim_message = MessageBase(
                message_info=base_message_info,
                message_segment=notify_seg,
                raw_message=description
            )
            
            # 发送到MaiBot Core
            if self.router:
                logger.debug(f"发送reaction事件到MaiBot: {description}")
                await self.router.send_message(maim_message)
                logger.debug("Reaction事件已成功发送到MaiBot")
            else:
                logger.error("Router未初始化，无法发送reaction事件")
                
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f"处理reaction事件时发生错误: {e}")
            logger.error(f"错误详情: {traceback.format_exc()}")


# 创建全局消息处理器实例
message_handler: DiscordMessageHandler = DiscordMessageHandler()
