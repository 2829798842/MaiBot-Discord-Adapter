import asyncio
import subprocess
import sys
import time
import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

try:
    # MaiBot 插件环境
    from src.common.logger import get_logger
    logger = get_logger("discord_adapter.dependency")
except ImportError:
    # 独立运行环境
    import logging
    logger = logging.getLogger("discord_adapter.dependency")


@dataclass
class PipMirror:
    name: str
    url: str
    host: str
    priority: int = 0
    latency_ms: float = float("inf")


PIP_MIRRORS: List[PipMirror] = [
    PipMirror(name="官方源", url="https://pypi.org/simple", host="pypi.org", priority=5),
    PipMirror(
        name="清华源", url="https://pypi.tuna.tsinghua.edu.cn/simple", host="pypi.tuna.tsinghua.edu.cn", priority=1
    ),
    PipMirror(name="阿里源", url="https://mirrors.aliyun.com/pypi/simple", host="mirrors.aliyun.com", priority=2),
    PipMirror(
        name="中科源", url="https://pypi.mirrors.ustc.edu.cn/simple", host="pypi.mirrors.ustc.edu.cn", priority=3
    ),
    PipMirror(name="上交源", url="https://mirror.sjtu.edu.cn/pypi/web/simple", host="mirror.sjtu.edu.cn", priority=4),
]

IMPORT_NAME_MAP = {
    "discord.py": "discord",
    "discord-ext-voice-recv": "discord.ext.voice_recv",
    "azure-cognitiveservices-speech": "azure.cognitiveservices.speech",
    "maim_message": "maim_message",
}


def get_import_name(package: str) -> str:
    """将包名转换为导入名"""
    clean = package.split("[")[0].split(">=")[0].split("==")[0].split("<")[0]
    return IMPORT_NAME_MAP.get(clean, clean.replace("-", "_"))


def check_package_installed(package: str) -> bool:
    """检查单个包是否已安装"""
    import_name = get_import_name(package)
    try:
        spec = importlib.util.find_spec(import_name.split(".")[0])
        return spec is not None
    except (ModuleNotFoundError, ValueError):
        return False


def get_missing_packages(packages: List[str]) -> List[str]:
    """获取未安装的包列表"""
    return [p for p in packages if not check_package_installed(p)]


def get_requirements_path() -> Path:
    return Path(__file__).parent / "requirements.txt"


def load_dependencies() -> List[str]:
    """从 requirements.txt 加载依赖列表"""
    req_path = get_requirements_path()
    if not req_path.exists():
        return []
    deps = []
    with open(req_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                deps.append(line)
    return deps


def check_uv_available() -> bool:
    try:
        result = subprocess.run(["uv", "--version"], capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


async def test_mirror_speed(mirror: PipMirror, timeout: float = 5.0) -> float:
    """测试镜像速度"""
    try:
        import aiohttp

        start = time.monotonic()
        async with aiohttp.ClientSession() as session:
            test_url = f"{mirror.url}/pip/"
            async with session.head(test_url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status < 500:
                    return (time.monotonic() - start) * 1000
    except Exception:
        pass
    return float("inf")


async def find_fastest_mirror(show_progress: bool = True) -> Optional[PipMirror]:
    if show_progress:
        logger.info("正在测试 pip 镜像源速度...")

    tasks = [test_mirror_speed(m) for m in PIP_MIRRORS]
    results = await asyncio.gather(*tasks)

    for i, mirror in enumerate(PIP_MIRRORS):
        mirror.latency_ms = results[i] if isinstance(results[i], float) else float("inf")
        if show_progress:
            if mirror.latency_ms < float("inf"):
                logger.info(f"  {mirror.name}: {mirror.latency_ms:.0f}ms")
            else:
                logger.info(f"  {mirror.name}: 不可用")

    available = [m for m in PIP_MIRRORS if m.latency_ms < float("inf")]
    if available:
        best = min(available, key=lambda m: m.latency_ms)
        logger.info(f"选择最快镜像: {best.name} ({best.latency_ms:.0f}ms)")
        return best

    logger.warning("所有镜像测试失败，使用阿里源作为默认")
    return PIP_MIRRORS[2]


async def install_dependencies(packages: List[str], use_uv: bool = True, auto_select_mirror: bool = True) -> bool:
    if not packages:
        return True

    mirror_url = None
    if auto_select_mirror:
        try:
            best = await find_fastest_mirror()
            if best:
                mirror_url = best.url
        except Exception as e:
            logger.warning(f"镜像测速失败: {e}")

    if use_uv and check_uv_available():
        cmd = ["uv", "pip", "install"]
    else:
        cmd = [sys.executable, "-m", "pip", "install"]

    if mirror_url:
        host = mirror_url.split("//")[1].split("/")[0]
        cmd.extend(["-i", mirror_url, "--trusted-host", host])

    cmd.extend(packages)

    logger.info(f"安装依赖: {' '.join(packages)}")

    try:
        result = subprocess.run(cmd, timeout=600)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        logger.error("依赖安装超时")
        return False
    except Exception as e:
        logger.error(f"依赖安装错误: {e}")
        return False


def check_dependencies() -> Tuple[bool, List[str]]:
    """检查依赖是否都已安装，返回 (是否全部安装, 缺失列表)"""
    deps = load_dependencies()
    missing = get_missing_packages(deps)
    return len(missing) == 0, missing


async def prompt_and_install_dependencies() -> bool:
    """检查依赖，如果有缺失则询问用户是否自动安装。"""
    all_installed, missing = check_dependencies()

    if all_installed:
        logger.info("所有依赖已安装")
        return True

    logger.warning(f"检测到 {len(missing)} 个缺失依赖:")
    for pkg in missing:
        logger.warning(f"  - {pkg}")

    try:
        answer = input("是否自动安装这些依赖? [y/N]: ").strip().lower()
        auto_install = answer in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        auto_install = False

    if not auto_install:
        logger.warning("跳过依赖安装，插件可能无法正常运行")
        return False

    success = await install_dependencies(missing)
    if success:
        logger.info("依赖安装完成，请重启 MaiBot")
    else:
        logger.error("部分依赖安装失败")
    return success
