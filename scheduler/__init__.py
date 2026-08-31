"""
定时任务系统 - DevProjectTeamSkill

轻量、可靠、可观测的本地定时任务调度框架。
基于 APScheduler 封装，提供幂等校验、重试策略、死信队列、
状态持久化、执行记录、告警通知等企业级特性。

用法：
    from tools.scheduler import register_task, get_scheduler

    @register_task(name="my_task", trigger="cron", hour=2, minute=0)
    def my_task():
        pass

    scheduler = get_scheduler()
    scheduler.start()
"""

from .models import TaskStatus, TaskResult, TaskContext
from .registry import task_registry, register_task
from .scheduler import get_scheduler, SchedulerManager

__version__ = "1.0.0"
__all__ = [
    "TaskStatus",
    "TaskResult",
    "TaskContext",
    "task_registry",
    "register_task",
    "get_scheduler",
    "SchedulerManager",
]
