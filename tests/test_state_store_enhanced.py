"""
state_store.py 增强功能测试：崩溃恢复 / 清理 / 统计 / 删除
"""

import pytest
from pathlib import Path

from scheduler.models import TaskDefinition, ExecutionRecord, TriggerType, TaskStatus
from scheduler.state_store import StateStore
from scheduler.utils import now


# ─── 崩溃恢复测试 ────────────────────────────────────────────

class TestCrashRecovery:
    """崩溃恢复测试"""

    def test_recover_running_executions(self, test_store, sample_task_def):
        """running 状态的执行记录被恢复为 failed"""
        # 注册任务
        job_id = test_store.upsert_job(sample_task_def)

        # 创建一条 running 状态的执行记录
        record = ExecutionRecord(
            run_id="RUN-CRASH-001",
            task_name=sample_task_def.name,
            trigger_type="schedule",
            status="running",
        )
        test_store.create_execution(record)

        # 执行恢复
        summary = test_store.recover_from_crash()

        # 验证
        assert summary["recovered_executions"] == 1
        exec_record = test_store.get_execution("RUN-CRASH-001")
        assert exec_record.status == "failed"
        assert exec_record.error_code == "CRASH_RECOVERY"

    def test_recover_running_jobs(self, test_store, sample_task_def):
        """running 状态的任务被恢复为 active"""
        job_id = test_store.upsert_job(sample_task_def)
        test_store.update_job_status(sample_task_def.name, "running")

        summary = test_store.recover_from_crash()

        assert sample_task_def.name in summary["recovered_jobs"]
        job = test_store.get_job(sample_task_def.name)
        assert job.status == "active"

    def test_recover_resets_scheduler_state(self, test_store):
        """调度器状态被重置为 stopped"""
        test_store.mark_started("1.0.0")
        state = test_store.get_scheduler_state()
        assert state["state"] == "running"

        test_store.recover_from_crash()

        state = test_store.get_scheduler_state()
        assert state["state"] == "stopped"

    def test_recover_no_residual_state(self, test_store):
        """无残留状态时返回空摘要"""
        summary = test_store.recover_from_crash()
        assert summary["recovered_executions"] == 0
        assert summary["recovered_jobs"] == []
        assert summary["state_reset"] is True

    def test_recover_multiple_running(self, test_store, sample_task_def, sample_task_def_interval):
        """多条 running 记录全部恢复"""
        test_store.upsert_job(sample_task_def)
        test_store.upsert_job(sample_task_def_interval)

        for i in range(3):
            record = ExecutionRecord(
                run_id=f"RUN-CRASH-MULTI-{i}",
                task_name=sample_task_def.name,
                trigger_type="schedule",
                status="running",
            )
            test_store.create_execution(record)

        summary = test_store.recover_from_crash()
        assert summary["recovered_executions"] == 3

    def test_recover_preserves_completed(self, test_store, sample_task_def):
        """已完成的执行记录不被恢复影响"""
        test_store.upsert_job(sample_task_def)

        # 创建 success 和 failed 记录
        for status in ["success", "failed"]:
            record = ExecutionRecord(
                run_id=f"RUN-COMPLETE-{status}",
                task_name=sample_task_def.name,
                trigger_type="schedule",
                status=status,
            )
            test_store.create_execution(record)
            test_store.update_execution(f"RUN-COMPLETE-{status}", status=status)

        # 创建 running 记录
        running_record = ExecutionRecord(
            run_id="RUN-CRASH-PRESERVE",
            task_name=sample_task_def.name,
            trigger_type="schedule",
            status="running",
        )
        test_store.create_execution(running_record)

        test_store.recover_from_crash()

        # 已完成的保持不变
        assert test_store.get_execution("RUN-COMPLETE-success").status == "success"
        assert test_store.get_execution("RUN-COMPLETE-failed").status == "failed"
        # running 被恢复
        assert test_store.get_execution("RUN-CRASH-PRESERVE").status == "failed"


# ─── 统计查询测试 ────────────────────────────────────────────

class TestExecutionStats:
    """执行统计测试"""

    def test_stats_empty(self, test_store):
        """空数据库统计"""
        stats = test_store.get_execution_stats()
        assert stats["total"] == 0
        assert stats["success_rate"] == 0.0
        assert stats["avg_duration"] == 0.0

    def test_stats_with_records(self, test_store, sample_task_def):
        """有记录时的统计"""
        test_store.upsert_job(sample_task_def)

        # 创建不同状态的记录
        for i, status in enumerate(["success", "success", "failed", "dlq"]):
            record = ExecutionRecord(
                run_id=f"RUN-STAT-{i}",
                task_name=sample_task_def.name,
                trigger_type="schedule",
                status=status,
            )
            eid = test_store.create_execution(record)
            test_store.update_execution(
                f"RUN-STAT-{i}",
                status=status,
                duration=float(i + 1),
            )

        stats = test_store.get_execution_stats()
        assert stats["total"] == 4
        assert stats["success"] == 2
        assert stats["failed"] == 1
        assert stats["dlq"] == 1
        assert stats["success_rate"] == 50.0

    def test_stats_retry_rate(self, test_store, sample_task_def):
        """重试率统计"""
        test_store.upsert_job(sample_task_def)

        # 2 条记录，1 条有重试
        for i, retry_count in enumerate([0, 3]):
            record = ExecutionRecord(
                run_id=f"RUN-RETRY-{i}",
                task_name=sample_task_def.name,
                trigger_type="schedule",
                status="success",
                retry_count=retry_count,
            )
            test_store.create_execution(record)
            test_store.update_execution(f"RUN-RETRY-{i}", status="success")

        stats = test_store.get_execution_stats()
        assert stats["retry_rate"] == 50.0

    def test_task_stats(self, test_store, sample_task_def):
        """单任务统计"""
        test_store.upsert_job(sample_task_def)

        for i in range(3):
            record = ExecutionRecord(
                run_id=f"RUN-TSTAT-{i}",
                task_name=sample_task_def.name,
                trigger_type="schedule",
                status="success",
            )
            test_store.create_execution(record)
            test_store.update_execution(f"RUN-TSTAT-{i}", status="success", duration=1.5)

        stats = test_store.get_task_stats(sample_task_def.name)
        assert stats["task_name"] == sample_task_def.name
        assert stats["total"] == 3
        assert stats["success"] == 3
        assert stats["success_rate"] == 100.0
        assert stats["avg_duration"] == 1.5

    def test_task_stats_not_found(self, test_store):
        """不存在的任务返回错误"""
        stats = test_store.get_task_stats("nonexistent_task")
        assert "error" in stats


# ─── 删除任务测试 ────────────────────────────────────────────

class TestDeleteJob:
    """任务删除测试"""

    def test_delete_existing_job(self, test_store, sample_task_def):
        """删除已存在的任务"""
        test_store.upsert_job(sample_task_def)
        assert test_store.get_job(sample_task_def.name) is not None

        result = test_store.delete_job(sample_task_def.name)
        assert result is True
        assert test_store.get_job(sample_task_def.name) is None

    def test_delete_nonexistent_job(self, test_store):
        """删除不存在的任务返回 False"""
        result = test_store.delete_job("nonexistent_job")
        assert result is False

    def test_count_jobs(self, test_store, sample_task_def, sample_task_def_interval):
        """任务数量统计"""
        assert test_store.count_jobs() == 0

        test_store.upsert_job(sample_task_def)
        test_store.upsert_job(sample_task_def_interval)
        assert test_store.count_jobs() == 2

        test_store.update_job_status(sample_task_def.name, "paused")
        assert test_store.count_jobs("active") == 1
        assert test_store.count_jobs("paused") == 1


# ─── 幂等键清理测试 ──────────────────────────────────────────

class TestIdempotencyCleanup:
    """幂等键清理测试"""

    def test_cleanup_expired_keys(self, test_store, sample_task_def):
        """清理过期幂等键"""
        job_id = test_store.upsert_job(sample_task_def)

        # 记录幂等键
        test_store.record_idempotency(job_id, "old-key-1", {"result": "old"})
        test_store.record_idempotency(job_id, "old-key-2", {"result": "old"})

        # 手动修改 created_at 使其过期（超过 90 天）
        from datetime import timedelta
        old_date = (now() - timedelta(days=100)).isoformat()
        with test_store._transaction() as conn:
            conn.execute("UPDATE idempotency_keys SET created_at = ?", (old_date,))

        count = test_store.cleanup_expired_idempotency_keys(days=90)
        assert count == 2

    def test_cleanup_preserves_recent_keys(self, test_store, sample_task_def):
        """保留未过期的幂等键"""
        job_id = test_store.upsert_job(sample_task_def)

        test_store.record_idempotency(job_id, "recent-key", {"result": "recent"})

        count = test_store.cleanup_expired_idempotency_keys(days=90)
        assert count == 0

        # 键仍然存在
        result = test_store.check_idempotency("recent-key")
        assert result is not None
