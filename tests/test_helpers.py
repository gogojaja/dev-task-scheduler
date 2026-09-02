"""
测试辅助函数

提供单元测试用的示例任务函数。
"""

from __future__ import annotations

from scheduler.models import TaskResult, TaskContext


def sample_func():
    """最简单的成功任务"""
    return "ok"


def sample_func_with_context(context: TaskContext):
    """带 context 的成功任务"""
    return TaskResult.ok(
        message=f"ran {context.task_name}",
        data={"run_id": context.run_id},
    )


def sample_func_fail():
    """总是失败的任务"""
    raise RuntimeError("intentional test failure")


def sample_func_skip_retry():
    """业务错误，不重试"""
    return TaskResult.fail(
        message="business error",
        skip_retry=True,
    )


def sample_func_dict():
    """返回字典的任务"""
    return {"key": "value", "count": 42}


def sample_func_none():
    """返回 None 的任务"""
    return None


def sample_func_true():
    """返回 True 的任务"""
    return True
