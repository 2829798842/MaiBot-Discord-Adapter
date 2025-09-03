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
from .config import global_config

MAX_MESSAGE_LENGTH = 2000

class DiscordSendHandler:
    """Discord消息发送处理器
    
    负责将来自 MaiBot Core 的消息发送到 Discord
    
    Attributes:
        _channel_cache: 频道缓存字典，提高获取效率
        _user_cache: 用户缓存字典，提高获取效率
        _thread_context_map: 子区上下文映射，记录父频道到活跃子区的映射
    """

    def __init__(self):
        """初始化Discord发送处理器"""
        self._channel_cache = {}  # 频道缓存
        self._user_cache = {}     # 用户缓存
        self._thread_context_map = {}  # 子区上下文映射 {parent_channel_id: thread_id}

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

    def update_thread_context(self, parent_channel_id: str, thread_id: str):
        """更新子区上下文映射
        
        记录父频道到活跃子区的映射关系，用于后续回复路由
        
        Args:
            parent_channel_id: 父频道ID
            thread_id: 子区ID  
        """
        self._thread_context_map[parent_channel_id] = thread_id
        logger.debug(f"更新子区上下文映射: {parent_channel_id} -> {thread_id}")

    def clear_thread_context(self, parent_channel_id: str):
        """清除父频道的子区上下文映射
        
        当用户直接在父频道发消息时，清除子区映射以确保回复发送到父频道
        
        Args:
            parent_channel_id: 父频道ID
        """
        if parent_channel_id in self._thread_context_map:
            old_thread_id = self._thread_context_map.pop(parent_channel_id)
            logger.debug(f"清除子区上下文映射: {parent_channel_id} (之前映射到 {old_thread_id})")

    def get_active_thread(self, parent_channel_id: str) -> str | None:
        """获取父频道的活跃子区ID
        
        Args:
            parent_channel_id: 父频道ID
            
        Returns:
            str | None: 活跃子区ID，如果没有则返回None
        """
        return self._thread_context_map.get(parent_channel_id)

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
                # 服务器消息，group_id 可能是频道ID或Thread ID
                target_id = int(message_info.group_info.group_id)

                # 首先检查消息中是否包含子区路由信息
                thread_routing_info = self._extract_thread_routing_info(maim_message.message_segment)
                if thread_routing_info:
                    # 如果有子区路由信息，优先使用子区作为目标
                    thread_id = int(thread_routing_info["original_thread_id"])
                    thread_channel = discord_client.client.get_channel(thread_id)
                    if thread_channel and isinstance(thread_channel, discord.Thread):
                        logger.debug(f"使用子区路由信息发送到子区: {thread_channel.name} (ID: {thread_id})")
                        return thread_channel
                    else:
                        logger.warning(f"子区路由目标无效，回退到父频道: {thread_id}")

                # 如果没有路由信息但启用了记忆继承，检查上下文映射
                if global_config.chat.inherit_channel_memory:
                    active_thread_id = self.get_active_thread(str(target_id))
                    if active_thread_id:
                        # 检查是否有回复信息，如果回复的是子区消息，才使用子区路由
                        reply_message_id = self._extract_reply_message_id(maim_message.message_segment)
                        should_use_thread = True
                        
                        if reply_message_id:
                            # 如果有回复，检查被回复的消息是否在子区中
                            try:
                                # 尝试在父频道中查找被回复的消息
                                parent_channel = discord_client.client.get_channel(target_id)
                                if parent_channel:
                                    try:
                                        await parent_channel.fetch_message(int(reply_message_id))
                                        # 如果在父频道找到了被回复的消息，说明应该回复到父频道
                                        should_use_thread = False
                                        logger.debug(f"回复的消息在父频道中，回复到父频道: {reply_message_id}")
                                    except discord.NotFound:
                                        # 在父频道找不到，可能在子区中，继续使用子区路由
                                        logger.debug(f"回复的消息不在父频道中，使用子区路由: {reply_message_id}")
                            except Exception as e:
                                logger.debug(f"检查回复消息位置时出错，使用默认子区路由: {e}")
                        
                        if should_use_thread:
                            thread_channel = discord_client.client.get_channel(int(active_thread_id))
                            if thread_channel and isinstance(thread_channel, discord.Thread):
                                logger.debug(f"使用上下文映射发送到活跃子区: {thread_channel.name} (ID: {active_thread_id})")
                                return thread_channel
                            else:
                                logger.warning(f"映射的子区无效，清除映射: {active_thread_id}")
                                # 清除无效映射
                                if str(target_id) in self._thread_context_map:
                                    del self._thread_context_map[str(target_id)]

                # 检查是否有回复信息，如果有回复且启用了记忆继承，可能需要找到原始子区
                reply_message_id = self._extract_reply_message_id(maim_message.message_segment)
                original_thread_channel = None
                
                if reply_message_id and global_config.chat.inherit_channel_memory:
                    # 如果有回复消息且启用记忆继承，尝试找到原始消息所在的子区
                    original_thread_channel = await self._find_thread_by_message_id(reply_message_id, target_id)
                    if original_thread_channel:
                        logger.debug(f"通过回复消息找到原始子区: {original_thread_channel.name} (ID: {original_thread_channel.id})")

                # 优先从缓存获取目标频道
                channel = self._channel_cache.get(target_id)
                if not channel:
                    channel = discord_client.client.get_channel(target_id)
                    if channel:
                        self._cache_channel(target_id, channel)

                # 如果找到了原始子区，优先使用子区
                if original_thread_channel:
                    return original_thread_channel

                # 检查是否为Thread
                if channel and hasattr(channel, 'parent') and channel.parent is not None:
                    # 这是一个Thread
                    if isinstance(channel, discord.Thread):
                        # 检查Thread权限
                        bot_permissions = channel.permissions_for(channel.guild.me)
                        if not bot_permissions.send_messages_in_threads:
                            logger.warning(f"没有权限在子区 {channel.name} (ID: {target_id}) 发送消息")
                            return None

                        thread_name = channel.name
                        parent_channel_name = channel.parent.name
                        guild_name = channel.guild.name
                        logger.debug(
                            f"找到目标子区: {thread_name} (ID: {channel.id}) "
                            f"父频道: {parent_channel_name} 在服务器 {guild_name}"
                        )
                        return channel
                    else:
                        logger.warning(f"频道 {target_id} 不是有效的Thread")
                        return None

                elif channel and isinstance(channel, discord.TextChannel):
                    # 这是一个普通频道
                    # 检查是否有发送权限
                    bot_permissions = channel.permissions_for(channel.guild.me)
                    if not bot_permissions.send_messages:
                        logger.warning(f"没有权限在频道 {channel.name} (ID: {target_id}) 发送消息")
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
                    logger.warning(f"找不到频道/子区 {target_id} 或类型不正确")

                    try:
                        channel = await discord_client.client.fetch_channel(target_id)
                        
                        if isinstance(channel, discord.Thread):
                            # 获取到Thread
                            bot_permissions = channel.permissions_for(channel.guild.me)
                            if bot_permissions.send_messages_in_threads:
                                logger.debug(f"从API获取到子区: {channel.name} (ID: {channel.id})")
                                self._cache_channel(target_id, channel)
                                return channel
                            else:
                                logger.warning(f"没有权限在子区 {channel.name} (ID: {target_id}) 发送消息")
                        elif isinstance(channel, discord.TextChannel):
                            # 获取到普通频道
                            if channel.permissions_for(channel.guild.me).send_messages:
                                logger.debug(f"从API获取到频道: {channel.name} (ID: {channel.id})")
                                self._cache_channel(target_id, channel)
                                return channel
                            else:
                                logger.warning(f"没有权限在频道 {channel.name} (ID: {target_id}) 发送消息")
                        else:
                            logger.warning(f"目标 {target_id} 不是文本频道或子区")
                    except discord.NotFound:
                        logger.error(f"频道/子区 {target_id} 不存在")
                    except discord.Forbidden:
                        logger.error(f"没有权限访问频道/子区 {target_id}")
                    except discord.HTTPException as e:
                        logger.error(f"获取频道/子区时发生错误: {e}")

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
                        self._cache_user(user_id, user)
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
            # 跳过路由信息段，不作为消息内容处理
            if seg.type == "thread_context":
                logger.debug("跳过子区路由信息段")
                return
                
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

            # 待修正
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
            elif seg.type == "video":
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

    def _extract_thread_routing_info(self, message_segment: Seg) -> dict | None:
        """提取子区路由信息
        
        从消息段中递归查找 thread_context 类型的段，并提取其中的路由信息。
        
        Args:
            message_segment: MaiBot消息段对象
            
        Returns:
            dict | None: 子区路由信息字典，找不到时返回None
        """
        def extract_from_segment(seg: Seg) -> dict | None:
            if seg.type == "thread_context":
                # 找到子区上下文信息
                if isinstance(seg.data, dict):
                    logger.debug(f"找到子区路由信息: {seg.data}")
                    return seg.data
                else:
                    logger.warning(f"子区路由信息格式错误: {seg.data}")
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

    async def _find_thread_by_message_id(self, message_id: str, parent_channel_id: int) -> discord.Thread | None:
        """通过消息ID找到消息所在的子区
        
        Args:
            message_id: 要查找的消息ID
            parent_channel_id: 父频道ID
            
        Returns:
            discord.Thread | None: 找到的子区，未找到时返回None
        """
        try:
            message_id_int = int(message_id)
            
            # 先获取父频道
            parent_channel = discord_client.client.get_channel(parent_channel_id)
            if not isinstance(parent_channel, discord.TextChannel):
                return None
            
            # 遍历父频道的所有活跃子区
            for thread in parent_channel.threads:
                try:
                    # 尝试在子区中查找消息
                    message = await thread.fetch_message(message_id_int)
                    if message:
                        logger.debug(f"在子区 {thread.name} 中找到消息 {message_id}")
                        return thread
                except (discord.NotFound, discord.Forbidden):
                    # 消息不在这个子区中，继续查找
                    continue
                except discord.HTTPException:
                    # 其他HTTP错误，继续查找
                    continue
            
            # 如果在活跃子区中没找到，尝试获取归档的子区
            try:
                archived_threads = [thread async for thread in parent_channel.archived_threads(limit=50)]
                for thread in archived_threads:
                    try:
                        message = await thread.fetch_message(message_id_int)
                        if message:
                            logger.debug(f"在归档子区 {thread.name} 中找到消息 {message_id}")
                            return thread
                    except (discord.NotFound, discord.Forbidden):
                        continue
                    except discord.HTTPException:
                        continue
            except discord.HTTPException:
                logger.debug("无法访问归档子区")
            
            logger.debug(f"未能在父频道 {parent_channel_id} 的子区中找到消息 {message_id}")
            return None
            
        except (ValueError, AttributeError) as e:
            logger.error(f"查找子区时发生错误: {e}")
            return None


# 创建全局发送处理器实例
send_handler = DiscordSendHandler()
