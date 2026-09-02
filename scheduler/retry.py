"""  
重试策略模块

实现指数退避 + 抖动 + 最大重试次数 + 死信队列的重试策略。
区分临时错误（可重试）和业务错误（不可重试）。
支持重试事件回调和重试历史记录。
"""  

from __future__ import annotations

import random
from dataclasses import dataclass

from .config import get_config
from .utils import get_logger, now, format_datetime

logger = get_logger("scheduler.retry")


# ─── 重试事件回调 ────────────────────────────────────────────

class RetryEvent:
    """重试事件类型"""
    RETRYING = "retrying"        # 即将重试
    RETRY_FAILED = "retry_failed"  # 重试失败
    DLQ = "dlq"                  # 进入死信队列
    SUCCESS = "success"          # 重试后成功


class RetryCallback:
    """重试回调基类"""

    def on_retrying(self, task_name: str, retry_count: int, delay: float, error: str):
        """即将重试时回调"""
        pass

    def on_retry_failed(self, task_name: str, retry_count: int, error: str):
        """重试最终失败时回调"""
        pass

    def on_dlq(self, task_name: str, retry_count: int, error: str):
        """进入死信队列时回调"""
        pass

    def on_success_after_retry(self, task_name: str, retry_count: int):
        """重试后成功时回调"""
        pass


class LoggingRetryCallback(RetryCallback):
    """日志重试回调（默认）"""

    def on_retrying(self, task_name, retry_count, delay, error):
        logger.info(f"[{task_name}] Retrying ({retry_count}) in {delay:.1f}s: {error}")

    def on_retry_failed(self, task_name, retry_count, error):
        logger.error(f"[{task_name}] All retries exhausted ({retry_count}): {error}")

    def on_dlq(self, task_name, retry_count, error):
        logger.critical(f"[{task_name}] Entered DLQ after {retry_count} retries: {error}")

    def on_success_after_retry(self, task_name, retry_count):
        logger.info(f"[{task_name}] Succeeded after {retry_count} retries")


class RetryHistoryEntry:
    """重试历史条目"""

    def __init__(self, task_name: str, retry_count: int, event: str,
                 error: str = "", delay: float = 0):
        self.task_name = task_name
        self.retry_count = retry_count
        self.event = event
        self.error = error
        self.delay = delay
        self.timestamp = format_datetime(now())

    def to_dict(self) -> dict:
        return {
            "task_name": self.task_name,
            "retry_count": self.retry_count,
            "event": self.event,
            "error": self.error,
            "delay": self.delay,
            "timestamp": self.timestamp,
        }


@dataclass
class RetryPolicy:
    """重试策略配置"""
    max_retries: int = 3
    base_delay: int = 60          # 初始延迟（秒）
    max_delay: int = 3600         # 最大延迟（秒）
    jitter: int = 30              # 抖动范围（秒）
    backoff_factor: float = 2.0   # 指数因子

    # 回调和歷史
    callbacks: list = None
    history: list = None

    def __post_init__(self):
        if self.callbacks is None:
            self.callbacks = [LoggingRetryCallback()]
        if self.history is None:
            self.history = []

    @classmethod
    def from_config(cls) -> "RetryPolicy":
        """从全局配置创建"""
        config = get_config()
        return cls(
            max_retries=config.execution.default_max_retries,
            base_delay=config.execution.retry_base_delay,
            max_delay=config.execution.retry_max_delay,
            jitter=config.execution.retry_jitter,
            backoff_factor=config.execution.retry_backoff_factor,
        )

    def calculate_delay(self, retry_count: int) -> float:
        """计算第 N 次重试的延迟时间

        公式：delay = min(base_delay * (factor ^ retry_count), max_delay) + random(0, jitter)

        Args:
            retry_count: 重试次数（从 0 开始计数）

        Returns:
            延迟秒数
        """
        base = self.base_delay * (self.backoff_factor ** retry_count)
        delay = min(base, self.max_delay)

        # 添加随机抖动，避免重试风暴
        if self.jitter > 0:
            delay += random.uniform(0, self.jitter)

        return round(delay, 2)

    def should_retry(self, retry_count: int, skip_retry: bool = False) -> bool:
        """判断是否应该重试

        Args:
            retry_count: 当前重试次数（已完成的重试次数）
            skip_retry: 是否跳过重试（业务错误）

        Returns:
            是否应该重试
        """
        if skip_retry:
            logger.debug("Skip retry: skip_retry=True (business error)")
            return False

        if retry_count >= self.max_retries:
            logger.debug(f"Skip retry: reached max retries ({self.max_retries})")
            return False

        return True

    def is_dead_letter(self, retry_count: int, skip_retry: bool = False) -> bool:
        """判断是否应该进入死信队列

        Args:
            retry_count: 当前重试次数
            skip_retry: 是否跳过重试

        Returns:
            是否进入死信
        """
        # 业务错误直接进死信
        if skip_retry:
            return True
        # 达到最大重试次数后进死信
        return retry_count >= self.max_retries

    # ─── 回调触发 ───────────────────────────────────────────

    def _fire_event(self, event: str, task_name: str, retry_count: int,
                    error: str = "", delay: float = 0):
        """触发重试事件"""
        # 记录历史
        entry = RetryHistoryEntry(task_name, retry_count, event, error, delay)
        self.history.append(entry)

        # 限制历史记录大小
        if len(self.history) > 1000:
            self.history = self.history[-500:]

        # 触发回调
        for cb in self.callbacks:
            try:
                if event == RetryEvent.RETRYING:
                    cb.on_retrying(task_name, retry_count, delay, error)
                elif event == RetryEvent.RETRY_FAILED:
                    cb.on_retry_failed(task_name, retry_count, error)
                elif event == RetryEvent.DLQ:
                    cb.on_dlq(task_name, retry_count, error)
                elif event == RetryEvent.SUCCESS:
                    cb.on_success_after_retry(task_name, retry_count)
            except Exception as e:
                logger.debug(f"Retry callback error: {e}")

    def notify_retrying(self, task_name: str, retry_count: int, error: str):
        """通知即将重试"""
        delay = self.calculate_delay(retry_count)
        self._fire_event(RetryEvent.RETRYING, task_name, retry_count, error, delay)
        return delay

    def notify_dlq(self, task_name: str, retry_count: int, error: str):
        """通知进入死信队列"""
        self._fire_event(RetryEvent.DLQ, task_name, retry_count, error)

    def notify_success_after_retry(self, task_name: str, retry_count: int):
        """通知重试后成功"""
        self._fire_event(RetryEvent.SUCCESS, task_name, retry_count)

    def notify_retry_failed(self, task_name: str, retry_count: int, error: str):
        """通知重试最终失败"""
        self._fire_event(RetryEvent.RETRY_FAILED, task_name, retry_count, error)

    def get_history(self, task_name: str = None) -> list[dict]:
        """获取重试历史

        Args:
            task_name: 按任务名过滤

        Returns:
            历史记录字典列表
        """
        if task_name:
            return [e.to_dict() for e in self.history if e.task_name == task_name]
        return [e.to_dict() for e in self.history]


def get_retry_policy() -> RetryPolicy:
    """获取全局重试策略"""
    return RetryPolicy.from_config()
