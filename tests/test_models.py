"""
models.py 单元测试

覆盖数据模型、状态枚举、错误码、TaskResult、TaskContext、TaskDefinition。
"""

import pytest
from scheduler.models import (
    TaskStatus, TriggerType, ErrorCode,
    TaskResult, TaskContext, TaskDefinition, ExecutionRecord,
)


class TestTaskStatus:
    """TaskStatus 枚举测试"""

    def test_is_finished_success(self):
        assert TaskStatus.is_finished(TaskStatus.SUCCESS) is True

    def test_is_finished_failed(self):
        assert TaskStatus.is_finished(TaskStatus.FAILED) is True

    def test_is_finished_dlq(self):
        assert TaskStatus.is_finished(TaskStatus.DLQ) is True

    def test_is_finished_skipped(self):
        assert TaskStatus.is_finished(TaskStatus.SKIPPED) is True

    def test_is_finished_cancelled(self):
        assert TaskStatus.is_finished(TaskStatus.CANCELLED) is True

    def test_is_not_finished_running(self):
        assert TaskStatus.is_finished(TaskStatus.RUNNING) is False

    def test_is_not_finished_pending(self):
        assert TaskStatus.is_finished(TaskStatus.PENDING) is False

    def test_is_active_scheduled(self):
        assert TaskStatus.is_active(TaskStatus.SCHEDULED) is True

    def test_is_active_running(self):
        assert TaskStatus.is_active(TaskStatus.RUNNING) is True

    def test_is_active_retrying(self):
        assert TaskStatus.is_active(TaskStatus.RETRYING) is True

    def test_is_not_active_success(self):
        assert TaskStatus.is_active(TaskStatus.SUCCESS) is False


class TestTriggerType:
    """TriggerType 枚举测试"""

    def test_cron(self):
        assert TriggerType("cron") == TriggerType.CRON

    def test_interval(self):
        assert TriggerType("interval") == TriggerType.INTERVAL

    def test_date(self):
        assert TriggerType("date") == TriggerType.DATE

    def test_invalid_trigger(self):
        with pytest.raises(ValueError):
            TriggerType("invalid")


class TestErrorCode:
    """ErrorCode 常量测试"""

    def test_success_code(self):
        assert ErrorCode.SUCCESS == "SCH-00-000"

    def test_config_error(self):
        assert ErrorCode.CONFIG_NOT_FOUND == "SCH-01-001"

    def test_execution_error(self):
        assert ErrorCode.EXECUTION_ERROR == "SCH-04-001"

    def test_timeout_error(self):
        assert ErrorCode.TASK_TIMEOUT == "SCH-04-002"

    def test_idempotency_error(self):
        assert ErrorCode.IDEMPOTENCY_CONFLICT == "SCH-05-001"

    def test_storage_error(self):
        assert ErrorCode.STORAGE_CONNECTION_FAILED == "SCH-06-001"


class TestTaskResult:
    """TaskResult 数据类测试"""

    def test_ok_default(self):
        result = TaskResult.ok()
        assert result.success is True
        assert result.message == ""
        assert result.data == {}
        assert result.error_code == ErrorCode.SUCCESS

    def test_ok_with_message(self):
        result = TaskResult.ok(message="done")
        assert result.success is True
        assert result.message == "done"

    def test_ok_with_data(self):
        result = TaskResult.ok(data={"key": "value"})
        assert result.data == {"key": "value"}

    def test_fail_default(self):
        result = TaskResult.fail(message="error")
        assert result.success is False
        assert result.message == "error"
        assert result.skip_retry is False
        assert result.error_code == ErrorCode.EXECUTION_ERROR

    def test_fail_skip_retry(self):
        result = TaskResult.fail(message="biz error", skip_retry=True)
        assert result.success is False
        assert result.skip_retry is True

    def test_fail_custom_error_code(self):
        result = TaskResult.fail(message="timeout", error_code=ErrorCode.TASK_TIMEOUT)
        assert result.error_code == ErrorCode.TASK_TIMEOUT


class TestTaskContext:
    """TaskContext 数据类测试"""

    def test_create_default(self):
        ctx = TaskContext.create(task_name="test")
        assert ctx.task_name == "test"
        assert ctx.run_id  # UUID 不为空
        assert ctx.retry_count == 0
        assert ctx.scheduled_time is not None
        assert ctx.start_time is not None

    def test_create_with_params(self):
        ctx = TaskContext.create(task_name="test", params={"key": "val"})
        assert ctx.params == {"key": "val"}

    def test_run_id_unique(self):
        ctx1 = TaskContext.create(task_name="test")
        ctx2 = TaskContext.create(task_name="test")
        assert ctx1.run_id != ctx2.run_id


class TestTaskDefinition:
    """TaskDefinition 数据类测试"""

    def test_defaults(self):
        td = TaskDefinition(name="t", func_ref="m:f")
        assert td.trigger_type == TriggerType.CRON
        assert td.max_retries == 3
        assert td.timeout == 300
        assert td.idempotency_key_expr == "{date}"
        assert td.status == "active"

    def test_custom_values(self):
        td = TaskDefinition(
            name="custom",
            func_ref="mod:func",
            trigger_type=TriggerType.INTERVAL,
            trigger_config={"minutes": 5},
            max_retries=5,
            timeout=60,
        )
        assert td.name == "custom"
        assert td.trigger_type == TriggerType.INTERVAL
        assert td.max_retries == 5
        assert td.timeout == 60


class TestExecutionRecord:
    """ExecutionRecord 数据类测试"""

    def test_defaults(self):
        rec = ExecutionRecord()
        assert rec.run_id == ""
        assert rec.task_name == ""
        assert rec.status == TaskStatus.RUNNING.value
        assert rec.duration == 0.0
        assert rec.retry_count == 0
