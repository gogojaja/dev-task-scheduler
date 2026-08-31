"""
示例任务

展示如何使用 @register_task 装饰器注册定时任务。
"""

from __future__ import annotations

import random
from datetime import datetime

from ..registry import register_task
from ..models import TaskResult, TaskContext


# ─── 示例 1：最简单的任务 ────────────────────────────────────

@register_task(
    name="example_hello",
    trigger="cron",
    minute="*/5",  # 每 5 分钟
    description="示例：问候任务",
    idempotency_key="{datetime}",
)
def hello_task():
    """最简单的任务，返回字符串即可"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"Hello from scheduler! Current time: {now}"


# ─── 示例 2：带 context 的任务 ───────────────────────────────

@register_task(
    name="example_with_context",
    trigger="interval",
    minutes=10,
    description="示例：带上下文的任务",
    idempotency_key="{task_name}:{date}",
)
def context_task(context: TaskContext):
    """任务函数可以接收 TaskContext 参数"""
    return TaskResult.ok(
        message=f"Task {context.task_name} running, retry count: {context.retry_count}",
        data={
            "run_id": context.run_id,
            "scheduled_time": context.scheduled_time.isoformat(),
            "params": context.params,
        },
    )


# ─── 示例 3：可能失败的任务（演示重试） ──────────────────────

@register_task(
    name="example_flaky",
    trigger="cron",
    minute="*/15",
    description="示例：随机失败的任务（演示重试机制）",
    idempotency_key="{run_id}",
    max_retries=3,
    timeout=30,
)
def flaky_task():
    """随机失败的任务，演示重试和死信机制"""
    if random.random() < 0.3:  # 30% 概率失败
        raise RuntimeError("Random failure for demo purposes")
    return "Success after some luck!"


# ─── 示例 4：业务错误（不重试，直接进死信） ──────────────────

@register_task(
    name="example_business_error",
    trigger="cron",
    hour=3,
    minute=0,
    description="示例：业务错误（不重试）",
    idempotency_key="{date}",
    max_retries=2,
)
def business_error_task():
    """业务错误应该返回 skip_retry=True，避免无意义的重试"""
    return TaskResult.fail(
        message="Invalid data format: missing required field 'id'",
        error_code="SCH-04-001",
        skip_retry=True,  # 业务错误，不重试
    )


# ─── 示例 5：每日统计报告任务 ────────────────────────────────

@register_task(
    name="example_daily_report",
    trigger="cron",
    hour=8,
    minute=0,
    description="示例：每日统计报告",
    idempotency_key="{date}",
    timeout=120,
)
def daily_report_task():
    """模拟生成每日统计报告"""
    # 实际项目中这里会读取数据、生成报告
    stats = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "total_tasks": 10,
        "success_rate": "95.2%",
        "avg_duration": "12.5s",
    }
    return TaskResult.ok(
        message="Daily report generated successfully",
        data=stats,
    )
