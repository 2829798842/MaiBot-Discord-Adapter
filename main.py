"""
    Discord 适配器主函数
"""
import asyncio
import sys
import signal
import traceback
from src.logger import logger
from src.recv_handler.discord_client import discord_client
from src.recv_handler.message_handler import message_handler
from src.send_handler.main_send_handler import send_handler
from src.mmc_com_layer import mmc_start_com, mmc_stop_com, router
from src.utils import async_task_manager
from src.config import global_config
from src.background_tasks import background_task_manager

# 消息队列
message_queue = asyncio.Queue()

# 全局关闭事件
shutdown_event = asyncio.Event()


async def message_process():
    """消息处理协程
    
    从队列中获取 Discord 消息并转发到 MaiBot Core
    """
    logger.info("消息处理器已启动")
    while not shutdown_event.is_set():
        try:
            # 使用超时避免无限等待
            message = await asyncio.wait_for(message_queue.get(), timeout=1.0)
            logger.debug(f"消息处理器: 开始处理消息 {message.id}")
            # 处理 Discord 消息
            await message_handler.handle_discord_message(message)
            message_queue.task_done()
            logger.debug(f"消息处理器: 消息 {message.id} 处理完成")
        except asyncio.TimeoutError:
            # 超时正常，继续检查关闭事件
            continue
        except Exception as e:
            logger.error(f"处理消息时发生错误: {e}")
            logger.error(f"错误详情: {traceback.format_exc()}")
            await asyncio.sleep(0.1)

    logger.info("消息处理器已停止")


async def discord_message_collector():
    """Discord 消息收集器
    
    从 Discord 客户端收集消息并放入队列
    """
    logger.info("消息收集器已启动，正在等待 Discord 消息...")
    message_check_count = 0
    while not shutdown_event.is_set():
        try:
            if not discord_client.message_queue.empty():
                message = await asyncio.wait_for(discord_client.message_queue.get(), timeout=0.1)
                await message_queue.put(message)
                discord_client.message_queue.task_done()
                logger.debug(f"消息收集器: 已将消息 {message.id} 从 Discord 队列转移到主队列")
            else:
                message_check_count += 1
                if message_check_count % 1000 == 0:  # 每 10 秒打印一次状态
                    if hasattr(discord_client, 'is_connected') and discord_client.is_connected:
                        logger.debug("消息收集器: 正在等待消息... ")
                    else:
                        logger.debug("消息收集器: Discord未连接，正在等待重连...")
                await asyncio.sleep(0.01)
        except asyncio.TimeoutError:
            # 超时正常，继续循环
            continue
        except Exception as e:
            logger.error(f"收集 Discord 消息时发生错误: {e}")
            logger.error(f"错误详情: {traceback.format_exc()}")
            await asyncio.sleep(0.1)

    logger.info("消息收集器已停止")


async def graceful_shutdown():
    """优雅关闭函数"""
    try:
        logger.info("正在关闭 Discord 适配器...")

        # 设置关闭事件
        shutdown_event.set()

        # 停止后台任务
        try:
            background_task_manager.stop_all_tasks()
            logger.info("后台任务已停止")
        except Exception as e:
            logger.error(f"停止后台任务时出错: {e}")

        # 关闭 Discord 客户端
        try:
            await asyncio.wait_for(discord_client.stop(), timeout=10)
            logger.info("Discord 客户端已关闭")
        except asyncio.TimeoutError:
            logger.warning("Discord 客户端关闭超时")
        except Exception as e:
            logger.error(f"关闭 Discord 客户端时出错: {e}")

        # 关闭 MaiBot 通信
        try:
            await asyncio.wait_for(mmc_stop_com(), timeout=10)
            logger.info("MaiBot 通信已关闭")
        except asyncio.TimeoutError:
            logger.warning("MaiBot 通信关闭超时")
        except asyncio.CancelledError:
            logger.debug("MaiBot 通信任务已被取消")
        except Exception as e:
            logger.error(f"关闭 MaiBot 通信时出错: {e}")

        # 取消管理器中的任务
        try:
            await async_task_manager.cancel_all()
        except Exception as e:
            logger.error(f"取消任务管理器时出错: {e}")

        # 取消剩余任务
        current_task = asyncio.current_task()
        tasks = [t for t in asyncio.all_tasks() if t is not current_task and not t.done()]

        if tasks:
            logger.debug(f"取消 {len(tasks)} 个剩余任务...")
            for task in tasks:
                task.cancel()

            # 等待任务取消完成
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=5
                )
            except asyncio.TimeoutError:
                logger.warning("部分任务取消超时")
            except Exception as e:
                logger.debug(f"任务取消时出现异常: {e}")

        logger.info("Discord 适配器已成功关闭")

    except Exception as e:
        logger.error(f"关闭适配器时出现错误: {e}")


def setup_signal_handlers():
    """设置信号处理器"""
    def signal_handler(signum, frame):
        logger.info(f"接收到信号 {signum}，准备关闭适配器...")
        shutdown_event.set()

    # 设置信号处理器
    signal.signal(signal.SIGINT, signal_handler)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, signal_handler)
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, signal_handler)


async def run_adapter():
    """运行适配器的主函数"""
    try:
        # 设置消息处理器的路由器
        message_handler.router = router

        # 设置消息处理器和发送处理器的相互引用（用于子区上下文映射）
        message_handler.send_handler = send_handler

        # 注册 MaiBot 消息处理器
        router.register_class_handler(send_handler.handle_message)

        logger.info("正在启动 Discord 适配器...")

        # 创建所有任务
        tasks = []

        # 启动 MaiBot 通信
        try:
            mmc_task = asyncio.create_task(mmc_start_com())
            tasks.append(mmc_task)
            logger.info("MaiBot 通信任务已创建")
        except Exception as e:
            logger.error(f"创建 MaiBot 通信任务失败: {e}")
            return

        # 启动消息处理器
        try:
            message_task = asyncio.create_task(message_process())
            tasks.append(message_task)
            logger.info("消息处理任务已创建")
        except Exception as e:
            logger.error(f"创建消息处理任务失败: {e}")
            return

        # 启动消息收集器
        try:
            collector_task = asyncio.create_task(discord_message_collector())
            tasks.append(collector_task)
            logger.info("消息收集任务已创建")
        except Exception as e:
            logger.error(f"创建消息收集任务失败: {e}")
            return

        # 启动 Discord 客户端
        discord_task = None
        try:
            # 先启动 Discord 客户端
            discord_task = asyncio.create_task(discord_client.start())
            tasks.append(discord_task)
            logger.info("Discord 客户端任务已创建")

            # 等待一小段时间让Discord客户端开始连接
            await asyncio.sleep(2)

            # 然后初始化后台任务管理器并启动监控
            background_task_manager.register_connection_monitor(discord_client)
            background_task_manager.start_all_tasks()
            logger.info("后台任务管理器已初始化并启动")

        except Exception as e:
            logger.error(f"创建 Discord 客户端任务失败: {e}")
            # Discord 失败但仍然可以继续运行其他组件

        # 等待关闭信号或任务异常结束
        shutdown_task = asyncio.create_task(shutdown_event.wait())
        all_tasks = tasks + [shutdown_task]

        logger.info("所有任务已启动，等待运行...")

        # 等待任意任务完成
        done, pending = await asyncio.wait(
            all_tasks,
            return_when=asyncio.FIRST_COMPLETED
        )

        # 检查完成的任务
        for task in done:
            if task == shutdown_task:
                logger.info("收到关闭信号")
            else:
                # 检查是否是异常结束
                try:
                    if not task.cancelled():
                        task.result()  # 获取结果以检查异常
                        logger.warning(f"任务意外结束: {task}")
                except Exception as e:
                    if task == discord_task and "Cannot connect to host discord.com" in str(e):
                        logger.error("Discord 连接失败，可能是网络问题或 Token 无效")
                    else:
                        logger.error(f"任务异常结束: {e}")

        # 取消未完成的任务
        for task in pending:
            if not task.done():
                task.cancel()

        # 等待所有任务完成
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    except Exception as e:
        logger.error(f"运行适配器时发生错误: {e}")
    finally:
        # 确保优雅关闭
        await graceful_shutdown()


if __name__ == "__main__":
    # 检查配置
    try:
        if (not global_config.discord.token or
            global_config.discord.token == "your_discord_bot_token_"):
            logger.error("请在 config.toml 文件中设置有效的 Discord Bot Token")
            sys.exit(1)
    except Exception as e:
        logger.error(f"配置检查失败: {e}")
        sys.exit(1)

    # 设置信号处理器
    setup_signal_handlers()

    try:
        # 使用 asyncio.run 运行适配器
        logger.info("启动 Discord 适配器...")
        asyncio.run(run_adapter())

    except KeyboardInterrupt:
        logger.info("接收到键盘中断")
    except Exception as e:
        logger.exception(f"主程序异常: {str(e)}")
        sys.exit(1)
    finally:
        logger.info("程序已退出")
        sys.exit(0)
