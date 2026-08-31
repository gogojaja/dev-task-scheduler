"""
数据模型定义

包含任务状态、执行结果、执行上下文、错误码等核心数据模型。
"""

from __future__ import annotations

import uuid
import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, Any, Dict, List

DEFAULT_TIMEZONE = timezone(timedelta(hours=8))  # Asia/Shanghai


def _now() -> datetime:
    """获取当前时间（带时区）"""
    return datetime.now(DEFAULT_TIMEZONE)


class TaskStatus(str, enum.Enum):
    """任务执行状态"""
    PENDING = "pending"           # 待调度
    SCHEDULED = "scheduled"       # 已调度
    RUNNING = "running"           # 执行中
    SUCCESS = "success"           # 成功
    FAILED = "failed"             # 失败
    RETRYING = "retrying"         # 重试中
    DLQ = "dlq"                   # 死信队列
    SKIPPED = "skipped"           # 跳过（幂等去重）
    PAUSED = "paused"             # 已暂停
    CANCELLED = "cancelled"       # 已取消

    @classmethod
    def is_finished(cls, status: "TaskStatus") -> bool:
        """是否为终态"""
        return status in (cls.SUCCESS, cls.FAILED, cls.DLQ, cls.SKIPPED, cls.CANCELLED)

    @classmethod
    def is_active(cls, status: "TaskStatus") -> bool:
        """是否为活跃状态"""
        return status in (cls.SCHEDULED, cls.RUNNING, cls.RETRYING)


class TriggerType(str, enum.Enum):
    """触发器类型"""
    CRON = "cron"
    INTERVAL = "interval"
    DATE = "date"


class ErrorCode:
    """错误码规范 SCH-XX-YYY"""
    # 00 成功
    SUCCESS = "SCH-00-000"

    # 01 配置错误
    CONFIG_NOT_FOUND = "SCH-01-001"
    CONFIG_PARSE_ERROR = "SCH-01-002"
    CONFIG_INVALID = "SCH-01-003"

    # 02 注册错误
    TASK_DUPLICATE = "SCH-02-001"
    TASK_NOT_FOUND = "SCH-02-002"
    TASK_INVALID = "SCH-02-003"

    # 03 调度错误
    SCHEDULER_START_FAILED = "SCH-03-001"
    SCHEDULER_NOT_RUNNING = "SCH-03-002"
    MISFIRE_ERROR = "SCH-03-003"

    # 04 执行错误
    EXECUTION_ERROR = "SCH-04-001"
    TASK_TIMEOUT = "SCH-04-002"
    TASK_CANCELLED = "SCH-04-003"

    # 05 幂等错误
    IDEMPOTENCY_CONFLICT = "SCH-05-001"
    IDEMPOTENCY_DUPLICATE = "SCH-05-002"

    # 06 存储错误
    STORAGE_CONNECTION_FAILED = "SCH-06-001"
    STORAGE_OPERATION_FAILED = "SCH-06-002"

    # 07 超时错误
    TIMEOUT = "SCH-07-001"

    # 08 告警错误
    ALERT_FAILED = "SCH-08-001"

    # 09 权限错误
    PERMISSION_DENIED = "SCH-09-001"


@dataclass
class TaskResult:
    """任务执行结果

    Attributes:
        success: 是否成功
        message: 结果消息
        data: 结果数据（JSON 可序列化）
        skip_retry: 是否跳过重试（业务错误不重试）
    """
    success: bool
    message: str = ""
    data: dict = field(default_factory=dict)
    skip_retry: bool = False
    error_code: str = ErrorCode.SUCCESS

    @classmethod
    def ok(cls, message: str = "", data: dict = None) -> "TaskResult":
        """创建成功结果"""
        return cls(
            success=True,
            message=message,
            data=data or {},
            error_code=ErrorCode.SUCCESS,
        )

    @classmethod
    def fail(cls, message: str, error_code: str = ErrorCode.EXECUTION_ERROR,
             skip_retry: bool = False, data: dict = None) -> "TaskResult":
        """创建失败结果"""
        return cls(
            success=False,
            message=message,
            data=data or {},
            skip_retry=skip_retry,
            error_code=error_code,
        )


@dataclass
class TaskContext:
    """任务执行上下文

    注入到任务函数中，提供运行时信息。
    """
    task_name: str
    run_id: str
    scheduled_time: datetime
    start_time: datetime
    retry_count: int = 0
    idempotency_key: str = ""
    params: dict = field(default_factory=dict)

    @classmethod
    def create(cls, task_name: str, scheduled_time: datetime = None,
               params: dict = None) -> "TaskContext":
        """创建新的执行上下文"""
        current = _now()
        return cls(
            task_name=task_name,
            run_id=str(uuid.uuid4()),
            scheduled_time=scheduled_time or current,
            start_time=current,
            retry_count=0,
            params=params or {},
        )


@dataclass
class TaskDefinition:
    """任务定义

    Attributes:
        name: 任务名称（唯一标识）
        description: 任务描述
        func_ref: 函数引用路径 module:function
        trigger_type: 触发器类型
        trigger_config: 触发器配置
        max_retries: 最大重试次数
        timeout: 超时时间（秒）
        idempotency_key_expr: 幂等键表达式
        status: 任务状态
        params: 默认参数
    """
    name: str
    func_ref: str
    trigger_type: TriggerType = TriggerType.CRON
    trigger_config: dict = field(default_factory=dict)
    description: str = ""
    max_retries: int = 3
    timeout: int = 300
    idempotency_key_expr: str = "{date}"
    status: str = "active"
    params: dict = field(default_factory=dict)
    version: str = "1.0.0"


@dataclass
class ExecutionRecord:
    """执行记录

    对应 CSV 台账和 SQLite 执行记录表。
    """
    id: Optional[int] = None
    job_id: Optional[int] = None
    run_id: str = ""
    task_name: str = ""
    trigger_type: str = "schedule"
    scheduled_time: Optional[datetime] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration: float = 0.0
    status: str = TaskStatus.RUNNING.value
    retry_count: int = 0
    idempotency_key: str = ""
    error_code: str = ""
    error_message: str = ""
    result_data: dict = field(default_factory=dict)
    node_id: str = ""
    created_at: Optional[datetime] = None
