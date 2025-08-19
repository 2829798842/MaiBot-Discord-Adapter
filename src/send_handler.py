"""模块名称：消息发送处理器
主要功能：处理来自MaiBot Core的消息并发送到Discord
"""

import base64
import binascii
import io
import time
import traceback
import discord
from maim_message import MessageBase, Seg
from .logger import logger
from .recv_handler.discord_client import discord_client

MAX_MESSAGE_LENGTH = 2000

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

        except (AttributeError, TypeError, ValueError) as e:
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
            content_preview = (content[:100] + '...' if content and len(content) > 100
                             else content)
            logger.debug(f"  消息内容: {content_preview}")
            logger.debug(f"  文件数量: {len(files) if files else 0}")

            # 处理回复消息
            reference = await self._get_reply_reference(maim_message, target_channel)
            if reference:
                logger.debug(f"  回复消息ID: {reference.id}")

            # 发送消息
            if content or files:
                # 检查消息长度并分割发送
                await self._send_message_with_length_check(
                    target_channel, content, files, reference
                )
            else:
                logger.warning("消息内容为空，跳过发送")

        except (discord.HTTPException, discord.Forbidden, AttributeError) as e:
            logger.error(f"发送 Discord 消息时发生错误: {e}")
            logger.error(f"错误详情: {traceback.format_exc()}")

    async def _send_message_with_length_check(self, target_channel,
                                            content: str | None,
                                            files: list | None,
                                            reference):
        """检查消息长度并分割发送
        
        Args:
            target_channel: 目标频道
            content: 消息内容
            files: 文件列表
            reference: 回复引用
        """
        # Discord消息长度限制

        try:
            # 如果有文件，先发送文件（文件消息优先）
            if files:
                first_message = True
                for file in files:
                    # 第一个文件可以带文本内容和回复
                    if (first_message and content and
                        len(content) <= MAX_MESSAGE_LENGTH):
                        sent_message = await target_channel.send(
                            content=content,
                            file=file,
                            reference=reference
                        )
                        logger.debug(f"发送文件消息成功: {sent_message.id} (带内容)")
                        content = None  # 内容已发送，清空
                        first_message = False
                    else:
                        # 后续文件只发送文件本身
                        sent_message = await target_channel.send(file=file)
                        logger.debug(f"发送文件消息成功: {sent_message.id}")

            # 处理文本内容
            if content:
                if len(content) <= MAX_MESSAGE_LENGTH:
                    # 内容不超长，直接发送
                    sent_message = await target_channel.send(
                        content=content,
                        reference=reference if not files else None  # 如果已经有文件消息带了回复，这里就不重复了
                    )
                    logger.debug(f"发送文本消息成功: {sent_message.id}")
                else:
                    # 内容超长，需要分割发送
                    logger.warning(f"消息内容过长({len(content)}字符)，将分割发送")
                    await self._send_long_message(
                        target_channel, content, reference if not files else None
                    )

        except (discord.HTTPException, discord.Forbidden) as e:
            logger.error(f"分割发送消息时发生错误: {e}")
            logger.error(f"错误详情: {traceback.format_exc()}")

    async def _send_long_message(self, target_channel, content: str, reference):
        """分割发送长消息
        
        Args:
            target_channel: 目标频道
            content: 长消息内容
            reference: 回复引用
        """

        # 按行分割，尽量保持内容完整性
        lines = content.split('\n')
        current_message = ""
        message_count = 0

        for line in lines:
            # 检查单行是否过长
            if len(line) > MAX_MESSAGE_LENGTH:
                # 单行过长，需要强制分割
                if current_message:
                    # 先发送当前累积的消息
                    await self._send_single_message_part(
                        target_channel, current_message, reference, message_count
                    )
                    current_message = ""
                    message_count += 1

                # 分割长行
                while len(line) > MAX_MESSAGE_LENGTH:
                    part = line[:MAX_MESSAGE_LENGTH]
                    line = line[MAX_MESSAGE_LENGTH:]
                    await self._send_single_message_part(
                        target_channel, part, reference, message_count
                    )
                    message_count += 1

                # 剩余部分
                if line:
                    current_message = line
            else:
                # 检查添加这行后是否会超长
                test_message = current_message + '\n' + line if current_message else line
                if len(test_message) > MAX_MESSAGE_LENGTH:
                    # 会超长，先发送当前消息
                    if current_message:
                        await self._send_single_message_part(
                            target_channel, current_message, reference, message_count
                        )
                        message_count += 1
                    current_message = line
                else:
                    current_message = test_message

        # 发送最后一部分
        if current_message:
            await self._send_single_message_part(
                target_channel, current_message, reference, message_count
            )

    async def _send_single_message_part(self, target_channel, content: str,
                                       reference, part_number: int):
        """发送单个消息片段
        
        Args:
            target_channel: 目标频道
            content: 消息内容
            reference: 回复引用（只在第一条消息使用）
            part_number: 片段编号
        """
        try:
            sent_message = await target_channel.send(
                content=content,
                reference=reference if part_number == 0 else None  # 只有第一条消息带回复
            )
            logger.debug(f"发送消息片段成功: {sent_message.id} (第{part_number + 1}部分)")
        except (discord.HTTPException, discord.Forbidden) as e:
            logger.error(f"发送消息片段失败: {e}")

    async def _get_target_channel(self, maim_message: MessageBase) -> (
        discord.abc.Messageable | None):
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

                    channel_name = channel.name
                    guild_name = channel.guild.name
                    logger.debug(
                        f"找到目标频道: {channel_name} (ID: {channel.id}) "
                        f"在服务器 {guild_name}"
                    )
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

        except (AttributeError, ValueError) as e:
            logger.error(f"获取目标频道时发生错误: {e}")

        return None

    async def _process_message_content(self, message_segment: Seg) -> (
        tuple[str | None, list[discord.File] | None]):
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
            elif seg.type in ["emoji", "image"]:
                # dc中只有文件格式
                try:
                    image_data_str = str(seg.data)
                    logger.debug(f"处理{seg.type}数据，长度: {len(image_data_str)}")

                    # 尝试解码base64数据
                    try:
                        decoded_data = base64.b64decode(image_data_str)
                        logger.debug(f"成功解码{seg.type}的base64数据，大小: {len(decoded_data)} 字节")

                        # 检测图片格式
                        image_format = self._detect_image_format(decoded_data)
                        logger.debug(f"检测到{seg.type}格式: {image_format}")

                        # 设置文件名，使用时间戳确保唯一性
                        timestamp = int(time.time())
                        if image_format != 'unknown':
                            prefix = "emoji" if seg.type == "emoji" else "image"
                            filename = f"{prefix}_{timestamp}.{image_format}"
                        else:
                            # 即使格式未知，也强制保存为文件
                            prefix = "emoji" if seg.type == "emoji" else "image"
                            filename = f"{prefix}_{timestamp}.bin"
                            logger.warning(f"{seg.type}格式未知，保存为.bin文件")

                        # 创建Discord文件对象
                        files.append(discord.File(
                            fp=io.BytesIO(decoded_data),
                            filename=filename
                        ))
                        logger.info(f"成功处理{seg.type}文件: {filename}")

                    except (ValueError, TypeError, binascii.Error) as decode_error:
                        # base64解码失败，仍然尝试作为文件处理
                        logger.warning(f"{seg.type}base64解码失败: {decode_error}，尝试直接处理")
                        try:
                            # 将字符串转换为字节并保存
                            data_bytes = image_data_str.encode('utf-8')
                            timestamp = int(time.time())
                            prefix = "emoji" if seg.type == "emoji" else "image"
                            filename = f"{prefix}_{timestamp}.txt"

                            files.append(discord.File(
                                fp=io.BytesIO(data_bytes),
                                filename=filename
                            ))
                            logger.info(f"作为文本文件处理{seg.type}: {filename}")
                        except (UnicodeEncodeError, OSError) as final_error:
                            # 最终失败，添加文本说明
                            display_name = "表情" if seg.type == "emoji" else "图片"
                            content_parts.append(f"[{display_name}处理失败]")
                            logger.error(f"无法处理{seg.type}: {final_error}")

                except (AttributeError, TypeError) as e:
                    display_name = "表情" if seg.type == "emoji" else "图片"
                    logger.error(f"处理{display_name}时发生错误: {e}")
                    content_parts.append(f"[{display_name}]")
            elif seg.type == "voice":
                try:
                    # 解码 base64 语音
                    voice_data = base64.b64decode(seg.data)
                    files.append(discord.File(
                        fp=io.BytesIO(voice_data),
                        filename="voice.wav"
                    ))
                except (ValueError, TypeError, binascii.Error) as e:
                    logger.error(f"处理语音时发生错误: {e}")
            elif seg.type == "voiceurl":
                content_parts.append(f"[语音消息: {seg.data}]")
                # 照搬ncada，暂时无用
            elif seg.type == "music":
                content_parts.append(f"[音乐: {seg.data}]")
            elif seg.type == "videourl":
                content_parts.append(f"[视频: {seg.data}]")
            elif seg.type == "file":
                content_parts.append(f"[文件: {seg.data}]")
            elif seg.type == "command":
                content_parts.append(f"[命令: {seg.data}]")
            elif seg.type == "seglist" and isinstance(seg.data, list):
                for sub_seg in seg.data:
                    process_segment(sub_seg)

        process_segment(message_segment)

        content = "\n".join(content_parts) if content_parts else None
        return content, files if files else None

    async def _get_reply_reference(self, maim_message: MessageBase,
                                  channel: discord.abc.Messageable) -> (
                                      discord.Message | None):
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
                    author_name = original_message.author.display_name
                    logger.debug(
                        f"成功获取被回复消息: {original_message.id} by {author_name}"
                    )
                    return original_message
                except discord.NotFound:
                    logger.warning(f"被回复的消息已被删除: {reply_message_id}")
                except discord.Forbidden:
                    logger.warning(f"没有权限访问被回复的消息: {reply_message_id}")
                    return None
            else:
                # 回退到常规获取方式
                original_message = await channel.fetch_message(message_id_int)
                author_name = original_message.author.display_name
                logger.debug(
                    f"成功获取被回复消息: {original_message.id} by {author_name}"
                )
                return original_message

        except ValueError:
            logger.warning(f"回复消息ID格式无效: {reply_message_id}")
        except discord.HTTPException as e:
            logger.warning(f"Discord API错误: {e}")
        except (AttributeError, RuntimeError) as e:
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
                    except (ValueError, TypeError):
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
                    display_name = user_mention.get(
                        "display_name", user_mention.get("username", "未知用户")
                    )
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

        except (KeyError, AttributeError, TypeError) as e:
            logger.error(f"处理mention段时发生错误: {e}")
            return ""

    def _detect_image_format(self, image_data: bytes) -> str:
        """检测图片格式
        
        Args:
            image_data: 图片的二进制数据
            
        Returns:
            str: 图片格式（如 'png', 'jpg', 'gif', 'webp' 等），无法识别时返回 'unknown'
        """
        try:
            # 检查文件头来确定图片格式
            if image_data.startswith(b'\x89PNG\r\n\x1a\n'):
                return 'png'
            elif image_data.startswith(b'\xff\xd8\xff'):
                return 'jpg'
            elif image_data.startswith(b'GIF87a') or image_data.startswith(b'GIF89a'):
                return 'gif'
            elif (image_data.startswith(b'RIFF') and len(image_data) >= 12 and
                  image_data[8:12] == b'WEBP'):
                return 'webp'
            elif image_data.startswith(b'BM'):
                return 'bmp'
            elif (image_data.startswith(b'\x00\x00\x01\x00') or
                  image_data.startswith(b'\x00\x00\x02\x00')):
                return 'ico'
            else:
                # 无法识别的格式
                return 'unknown'
        except (AttributeError, IndexError) as e:
            logger.error(f"检测图片格式时发生错误: {e}")
            return 'unknown'


# 创建全局发送处理器实例
send_handler = DiscordSendHandler()
