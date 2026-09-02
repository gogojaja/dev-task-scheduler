"""
超时控制模块

提供多级超时控制策略：
1. 线程超时（默认，跨平台）
2. 信号超时（Unix only，SIGALRM）
3. 进程超时（subprocess，最强隔离）

超时级别：
- Level 1: thread.join(timeout) — 优雅等待线程结束
- Level 2: SIGALRM (Unix) — 信号中断
- Level 3: subprocess — 独立进程，可强制终止
"""

from __future__ import annotations

import os
import sys
import signal
import threading
import subprocess
import traceback
from typing import Callable, Optional, Any

from .models import TaskResult, TaskContext, ErrorCode
from .utils import get_logger, load_function, is_windows

logger = get_logger("scheduler.timeout")


class TimeoutError(Exception):
    """任务超时异常"""
    pass


def run_with_thread_timeout(
    func: Callable,
    args: tuple = (),
    kwargs: dict = None,
    timeout: int = 300,
) -> Any:
    """线程级超时控制（跨平台）

    在独立线程中执行函数，超时后返回 TimeoutError。
    注意：无法强制终止正在运行的线程（Python 限制）。

    Args:
        func: 要执行的函数
        args: 位置参数
        kwargs: 关键字参数
        timeout: 超时秒数

    Returns:
        函数返回值

    Raises:
        TimeoutError: 超时时
    """
    kwargs = kwargs or {}
    result_container: dict = {}
    exception_container: dict = {}

    def target():
        try:
            result_container["result"] = func(*args, **kwargs)
        except Exception as e:
            exception_container["exception"] = e
            exception_container["traceback"] = traceback.format_exc()

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        raise TimeoutError(f"Task timed out after {timeout} seconds (thread)")

    if "exception" in exception_container:
        raise exception_container["exception"]

    return result_container.get("result")


def run_with_signal_timeout(
    func: Callable,
    args: tuple = (),
    kwargs: dict = None,
    timeout: int = 300,
) -> Any:
    """信号级超时控制（仅 Unix）

    使用 SIGALRM 信号实现超时。
    注意：只能在主线程中使用，不支持 Windows。

    Args:
        func: 要执行的函数
        args: 位置参数
        kwargs: 关键字参数
        timeout: 超时秒数

    Returns:
        函数返回值

    Raises:
        TimeoutError: 超时时
        RuntimeError: 非主线程或非 Unix 系统时
    """
    if is_windows():
        raise RuntimeError("Signal timeout is not supported on Windows")

    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Signal timeout can only be used in the main thread")

    kwargs = kwargs or {}

    def handler(signum, frame):
        raise TimeoutError(f"Task timed out after {timeout} seconds (signal)")

    old_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, handler)
    signal.alarm(timeout)

    try:
        result = func(*args, **kwargs)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)

    return result


def run_with_process_timeout(
    func_ref: str,
    args: tuple = (),
    timeout: int = 300,
) -> Any:
    """进程级超时控制（最强隔离）

    在独立子进程中执行函数，超时后强制终止。
    适用于不可信代码或需要强隔离的场景。

    Args:
        func_ref: 函数引用（module:function 格式）
        args: 位置参数（必须可序列化）
        timeout: 超时秒数

    Returns:
        函数返回值

    Raises:
        TimeoutError: 超时时
        RuntimeError: 子进程异常时
    """
    # 构造子进程脚本
    script = f"""
import sys
import json
sys.path.insert(0, '.')
from scheduler.utils import load_function
func = load_function({func_ref!r})
result = func(*{args!r})
# 输出结果到 stdout
if result is not None:
    try:
        print(json.dumps({{"result": result}}))
    except (TypeError, ValueError):
        print(json.dumps({{"result": str(result)}}))
"""

    try:
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        if proc.returncode != 0:
            raise RuntimeError(
                f"Subprocess failed (code={proc.returncode}): {proc.stderr[:500]}"
            )

        stdout = proc.stdout.strip()
        if stdout:
            import json
            data = json.loads(stdout)
            return data.get("result")
        return None

    except subprocess.TimeoutExpired:
        raise TimeoutError(f"Task timed out after {timeout} seconds (process)")


def get_best_timeout_strategy() -> str:
    """获取当前环境最佳超时策略

    Returns:
        策略名称: 'signal' / 'thread'
    """
    if is_windows():
        return "thread"
    if threading.current_thread() is threading.main_thread():
        return "signal"
    return "thread"


def run_with_smart_timeout(
    func: Callable = None,
    func_ref: str = None,
    args: tuple = (),
    kwargs: dict = None,
    timeout: int = 300,
    strategy: str = "auto",
) -> Any:
    """智能超时控制

    根据环境和策略参数自动选择最佳超时方案。

    Args:
        func: 函数对象（与 func_ref 二选一）
        func_ref: 函数引用字符串（用于进程级超时）
        args: 位置参数
        kwargs: 关键字参数
        timeout: 超时秒数
        strategy: 超时策略 ('auto'/'thread'/'signal'/'process')

    Returns:
        函数返回值

    Raises:
        TimeoutError: 超时时
        ValueError: 参数无效时
    """
    kwargs = kwargs or {}

    if strategy == "auto":
        if func_ref and not is_windows():
            strategy = "process"
        else:
            strategy = get_best_timeout_strategy()

    if strategy == "signal":
        if func is None:
            raise ValueError("Signal strategy requires func parameter")
        return run_with_signal_timeout(func, args, kwargs, timeout)

    elif strategy == "process":
        if func_ref is None:
            raise ValueError("Process strategy requires func_ref parameter")
        return run_with_process_timeout(func_ref, args, timeout)

    else:  # thread (default)
        if func is None:
            raise ValueError("Thread strategy requires func parameter")
        return run_with_thread_timeout(func, args, kwargs, timeout)
