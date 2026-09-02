"""
scheduler.py + executor.py 增强功能测试：崩溃恢复集成 / 重试执行
"""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from scheduler.models import TaskDefinition, TriggerType, TaskResult, TaskStatus
from scheduler.executor import TaskExecutor
from scheduler.retry import RetryPolicy


class TestExecutorRetry:
    """带重试的执行测试"""

    def test_execute_with_retry_success_first_try(self, test_executor, tmp_path):
        """首次执行即成功，不重试"""
        # 创建一个会成功的任务
        task_def = TaskDefinition(
            name="retry_success",
            func_ref="tests.test_helpers:sample_func",
            trigger_type=TriggerType.CRON,
            trigger_config={"minute": "0"},
            max_retries=3,
            timeout=10,
        )
        test_executor.store.upsert_job(task_def)

        result = test_executor.execute_with_retry(task_def)
        assert result.success is True

    def test_execute_with_retry_fail_all(self, test_executor, tmp_path):
        """所有重试都失败，最终进入 DLQ"""
        task_def = TaskDefinition(
            name="retry_all_fail",
            func_ref="tests.test_helpers:sample_func_fail",
            trigger_type=TriggerType.CRON,
            trigger_config={"minute": "0"},
            max_retries=2,
            timeout=10,
            idempotency_key_expr="{run_id}",
        )
        test_executor.store.upsert_job(task_def)

        # 使用极短的重试延迟
        test_executor.retry_policy = RetryPolicy(
            max_retries=2, base_delay=0, max_delay=0, jitter=0, backoff_factor=1.0
        )

        result = test_executor.execute_with_retry(task_def)
        assert result.success is False

    def test_execute_with_retry_skip_retry_flag(self, test_executor, tmp_path):
        """skip_retry=True 直接进入 DLQ"""
        task_def = TaskDefinition(
            name="retry_skip",
            func_ref="tests.test_helpers:sample_func_skip_retry",
            trigger_type=TriggerType.CRON,
            trigger_config={"minute": "0"},
            max_retries=3,
            timeout=10,
            idempotency_key_expr="{run_id}",
        )
        test_executor.store.upsert_job(task_def)

        test_executor.retry_policy = RetryPolicy(
            max_retries=3, base_delay=0, max_delay=0, jitter=0, backoff_factor=1.0
        )

        result = test_executor.execute_with_retry(task_def)
        assert result.success is False


class TestSchedulerCrashRecovery:
    """调度器崩溃恢复集成测试"""

    def test_start_with_crash_recovery(self, test_store, sample_task_def):
        """启动时执行崩溃恢复"""
        # 模拟残留的 running 状态
        test_store.upsert_job(sample_task_def)
        from scheduler.models import ExecutionRecord
        record = ExecutionRecord(
            run_id="RUN-RECOVERY-001",
            task_name=sample_task_def.name,
            trigger_type="schedule",
            status="running",
        )
        test_store.create_execution(record)

        # 执行恢复
        summary = test_store.recover_from_crash()
        assert summary["recovered_executions"] == 1

        # 验证执行记录已被恢复
        exec_record = test_store.get_execution("RUN-RECOVERY-001")
        assert exec_record.status == "failed"
        assert exec_record.error_code == "CRASH_RECOVERY"

    def test_start_with_no_residual_state(self, test_store):
        """无残留状态时恢复摘要为空"""
        summary = test_store.recover_from_crash()
        assert summary["recovered_executions"] == 0
        assert summary["recovered_jobs"] == []


class TestSchedulerStats:
    """调度器统计测试"""

    def test_execution_stats_integration(self, test_store, sample_task_def):
        """执行统计集成测试"""
        test_store.upsert_job(sample_task_def)

        # 创建不同状态的执行记录
        for i, status in enumerate(["success", "success", "failed"]):
            from scheduler.models import ExecutionRecord
            record = ExecutionRecord(
                run_id=f"RUN-STAT-INT-{i}",
                task_name=sample_task_def.name,
                trigger_type="schedule",
                status=status,
            )
            test_store.create_execution(record)
            test_store.update_execution(f"RUN-STAT-INT-{i}", status=status, duration=1.0)

        stats = test_store.get_execution_stats()
        assert stats["total"] == 3
        assert stats["success"] == 2
        assert stats["failed"] == 1
        assert stats["success_rate"] == pytest.approx(66.67, abs=0.1)
