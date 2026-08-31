"""
工具函数

提供通用的工具函数：时间处理、ID 生成、日志配置、平台检测等。
"""

from __future__ import annotations

import os
import sys
import uuid
import socket
import logging
import platform
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional


# ─── 时间相关 ───────────────────────────────────────────────

DEFAULT_TIMEZONE = timezone(timedelta(hours=8))  # Asia/Shanghai


def now() -> datetime:
    """获取当前时间（带时区）"""
    return datetime.now(DEFAULT_TIMEZONE)


def now_str(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """获取当前时间字符串"""
    return now().strftime(fmt)


def today_str() -> str:
    """获取今天日期字符串 YYYY-MM-DD"""
    return now().strftime("%Y-%m-%d")


def format_datetime(dt: Optional[datetime], fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """格式化 datetime 为字符串"""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=DEFAULT_TIMEZONE)
    return dt.strftime(fmt)


def parse_datetime(s: str, fmt: str = "%Y-%m-%d %H:%M:%S") -> Optional[datetime]:
    """解析字符串为 datetime"""
    if not s:
        return None
    try:
        dt = datetime.strptime(s, fmt)
        return dt.replace(tzinfo=DEFAULT_TIMEZONE)
    except (ValueError, TypeError):
        return None


# ─── ID 生成 ────────────────────────────────────────────────

def gen_uuid() -> str:
    """生成 UUID"""
    return str(uuid.uuid4())


def gen_execution_id() -> str:
    """生成执行编号 EX-YYYYMMDD-XXXX"""
    date_part = today_str().replace("-", "")
    random_part = uuid.uuid4().hex[:4].upper()
    return f"EX-{date_part}-{random_part}"


def get_node_id() -> str:
    """获取节点标识（主机名）"""
    return socket.gethostname()


# ─── 平台检测 ───────────────────────────────────────────────

def get_os() -> str:
    """获取操作系统类型"""
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    elif system == "linux":
        return "linux"
    elif system == "windows":
        return "windows"
    return system


def is_macos() -> bool:
    return get_os() == "macos"


def is_linux() -> bool:
    return get_os() == "linux"


def is_windows() -> bool:
    return get_os() == "windows"


# ─── 路径工具 ───────────────────────────────────────────────

def get_project_root() -> Path:
    """获取项目根目录

    从 tools/scheduler/ 往上两级即为项目根
    """
    return Path(__file__).resolve().parent.parent.parent


def get_scheduler_dir() -> Path:
    """获取 scheduler 模块目录"""
    return Path(__file__).resolve().parent


def ensure_dir(path: Path) -> Path:
    """确保目录存在，不存在则创建"""
    path.mkdir(parents=True, exist_ok=True)
    return path


# ─── 日志配置 ───────────────────────────────────────────────

_LOGGER_CACHE: dict[str, logging.Logger] = {}


def get_logger(name: str = "scheduler", level: int = logging.INFO) -> logging.Logger:
    """获取日志器

    统一的日志格式和配置。
    """
    if name in _LOGGER_CACHE:
        return _LOGGER_CACHE[name]

    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    logger.propagate = False
    _LOGGER_CACHE[name] = logger
    return logger


# ─── 字符串工具 ─────────────────────────────────────────────

def safe_truncate(s: str, max_len: int = 500, suffix: str = "...") -> str:
    """安全截断字符串"""
    if not s:
        return s
    if len(s) <= max_len:
        return s
    return s[: max_len - len(suffix)] + suffix


def is_blank(s: Optional[str]) -> bool:
    """判断字符串是否为空或空白"""
    return s is None or s.strip() == ""


# ─── 函数加载 ───────────────────────────────────────────────

def load_function(func_ref: str):
    """从函数引用路径加载函数

    Args:
        func_ref: 格式 "module.path:function_name"

    Returns:
        函数对象

    Raises:
        ImportError: 模块或函数不存在
    """
    if ":" not in func_ref:
        raise ValueError(f"Invalid function reference format: {func_ref}, expected 'module:function'")

    module_path, func_name = func_ref.rsplit(":", 1)

    import importlib
    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise ImportError(f"Cannot import module '{module_path}': {e}") from e

    if not hasattr(module, func_name):
        raise AttributeError(f"Module '{module_path}' has no function '{func_name}'")

    return getattr(module, func_name)
