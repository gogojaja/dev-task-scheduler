"""
重试策略模块

实现指数退避 + 抖动 + 最大重试次数 + 死信队列的重试策略。
区分临时错误（可重试）和业务错误（不可重试）。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .config import get_config
from .utils import get_logger

logger = get_logger("scheduler.retry")


@dataclass
class RetryPolicy:
    """重试策略配置"""
    max_retries: int = 3
    base_delay: int = 60          # 初始延迟（秒）
    max_delay: int = 3600         # 最大延迟（秒）
    jitter: int = 30              # 抖动范围（秒）
    backoff_factor: float = 2.0   # 指数因子

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


def get_retry_policy() -> RetryPolicy:
    """获取全局重试策略"""
    return RetryPolicy.from_config()
