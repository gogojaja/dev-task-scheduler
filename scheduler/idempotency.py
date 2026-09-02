"""
幂等校验模块

框架层强制幂等校验，确保任务重复执行不产生重复副作用。
幂等键 = {task_name}:{business_key}，支持多种表达式。

支持的变量替换：
- {date} -> 日期 YYYY-MM-DD
- {datetime} -> 日期时间 YYYY-MM-DD HH:00:00（按小时）
- {task_name} -> 任务名称
- {run_id} -> 运行 ID
- {param:xxx} -> 任务参数中的 xxx 字段
- {weekday} -> 星期几 0-6（0=周一）
- {month} -> 月份
- {year} -> 年份
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from .models import TaskContext
from .state_store import get_state_store
from .utils import get_logger, today_str, safe_truncate

logger = get_logger("scheduler.idempotency")

# 幂等键最大长度（与数据库字段对齐）
MAX_KEY_LENGTH = 512

_PATTERN_DATE = re.compile(r"\{date\}")
_PATTERN_DATETIME = re.compile(r"\{datetime\}")
_PATTERN_TASK_NAME = re.compile(r"\{task_name\}")
_PATTERN_RUN_ID = re.compile(r"\{run_id\}")
_PATTERN_WEEKDAY = re.compile(r"\{weekday\}")
_PATTERN_MONTH = re.compile(r"\{month\}")
_PATTERN_YEAR = re.compile(r"\{year\}")
_PATTERN_PARAM = re.compile(r"\{param:(\w+)\}")


class IdempotencyManager:
    """幂等管理器

    负责幂等键生成、去重校验、结果记录。
    """

    def __init__(self, store=None):
        self.store = store or get_state_store()

    def generate_key(self, task_name: str, idempotency_expr: str,
                     context: TaskContext) -> str:
        """根据表达式生成幂等键

        Args:
            task_name: 任务名称
            idempotency_expr: 幂等键表达式
            context: 执行上下文

        Returns:
            生成的幂等键（已安全截断）

        Raises:
            ValueError: 当 task_name 为空时
        """
        if not task_name:
            raise ValueError("task_name cannot be empty for idempotency key generation")

        if not idempotency_expr:
            # 默认表达式：按日期去重
            idempotency_expr = "{date}"

        now = datetime.now()
        key = idempotency_expr

        # 替换变量
        key = _PATTERN_TASK_NAME.sub(task_name, key)
        key = _PATTERN_DATE.sub(today_str(), key)
        key = _PATTERN_DATETIME.sub(now.strftime("%Y-%m-%d %H:00:00"), key)
        key = _PATTERN_RUN_ID.sub(context.run_id, key)
        key = _PATTERN_WEEKDAY.sub(str(now.weekday()), key)
        key = _PATTERN_MONTH.sub(now.strftime("%m"), key)
        key = _PATTERN_YEAR.sub(now.strftime("%Y"), key)

        # 替换参数变量 {param:xxx}
        def replace_param(match):
            param_name = match.group(1)
            return str(context.params.get(param_name, ""))

        key = _PATTERN_PARAM.sub(replace_param, key)

        # 完整幂等键 = 任务名:生成的键
        full_key = f"{task_name}:{key}"

        # 安全截断防止超过数据库字段长度
        full_key = safe_truncate(full_key, MAX_KEY_LENGTH, suffix="")

        logger.debug(f"Generated idempotency key for '{task_name}': {full_key}")
        return full_key

    def check(self, idempotency_key: str) -> Optional[dict]:
        """检查幂等键是否已存在成功记录

        Args:
            idempotency_key: 幂等键

        Returns:
            存在则返回结果数据，不存在返回 None

        Raises:
            ValueError: 当幂等键为空时
        """
        if not idempotency_key or not idempotency_key.strip():
            raise ValueError("idempotency_key cannot be empty")

        result = self.store.check_idempotency(idempotency_key)
        if result is not None:
            logger.info(f"Idempotency hit, skip execution: {idempotency_key}")
        return result

    def check_or_skip(self, idempotency_key: str) -> tuple[bool, Optional[dict]]:
        """安全检查幂等键（不抛异常）

        Args:
            idempotency_key: 幂等键

        Returns:
            (should_skip, result_data) 元组
            - should_skip=True 表示已存在，应跳过执行
            - should_skip=False 表示不存在，应执行
        """
        if not idempotency_key or not idempotency_key.strip():
            # 空键不做幂等校验，直接执行
            return False, None

        result = self.store.check_idempotency(idempotency_key)
        if result is not None:
            logger.info(f"Idempotency hit, skip execution: {idempotency_key}")
            return True, result
        return False, None

    def record(self, job_id: int, idempotency_key: str,
               result_data: dict, execution_id: int = None) -> bool:
        """记录幂等键（成功后调用）

        Args:
            job_id: 任务 ID
            idempotency_key: 幂等键
            result_data: 结果数据
            execution_id: 执行记录 ID

        Returns:
            是否成功（冲突则返回 False）

        Raises:
            ValueError: 当幂等键为空时
        """
        if not idempotency_key or not idempotency_key.strip():
            raise ValueError("idempotency_key cannot be empty for recording")

        success = self.store.record_idempotency(
            job_id=job_id,
            idempotency_key=idempotency_key,
            result_data=result_data,
            execution_id=execution_id,
        )
        if not success:
            logger.warning(f"Idempotency key conflict: {idempotency_key}")
        return success

    def cleanup_expired(self, days: int = 90) -> int:
        """清理过期的幂等键

        Args:
            days: 保留天数

        Returns:
            清理的键数
        """
        return self.store.cleanup_expired_idempotency_keys(days=days)


# 全局单例
_idempotency_manager: Optional[IdempotencyManager] = None


def get_idempotency_manager() -> IdempotencyManager:
    """获取全局幂等管理器单例"""
    global _idempotency_manager
    if _idempotency_manager is None:
        _idempotency_manager = IdempotencyManager()
    return _idempotency_manager
