"""
state_store.py 单元测试

覆盖 SQLite CRUD、幂等键操作、调度器状态、清理。
"""

import pytest
import json
from datetime import datetime

from scheduler.models import TaskDefinition, ExecutionRecord, TriggerType, TaskStatus
from scheduler.state_store import StateStore


class TestStateStoreJobs:
    """任务定义 CRUD 测试"""

    def test_upsert_job_create(self, test_store, sample_task_def):
        job_id = test_store.upsert_job(sample_task_def)
        assert job_id > 0

    def test_upsert_job_update(self, test_store, sample_task_def):
        job_id_1 = test_store.upsert_job(sample_task_def)
        sample_task_def.description = "updated"
        job_id_2 = test_store.upsert_job(sample_task_def)
        assert job_id_1 == job_id_2

    def test_get_job(self, test_store, sample_task_def):
        test_store.upsert_job(sample_task_def)
        retrieved = test_store.get_job(sample_task_def.name)
        assert retrieved is not None
        assert retrieved.name == sample_task_def.name
        assert retrieved.func_ref == sample_task_def.func_ref

    def test_get_job_not_found(self, test_store):
        assert test_store.get_job("nonexistent") is None

    def test_list_jobs(self, test_store, sample_task_def):
        test_store.upsert_job(sample_task_def)
        jobs = test_store.list_jobs()
        assert len(jobs) >= 1

    def test_list_jobs_by_status(self, test_store, sample_task_def):
        test_store.upsert_job(sample_task_def)
        active_jobs = test_store.list_jobs(status="active")
        assert len(active_jobs) >= 1

    def test_update_job_status(self, test_store, sample_task_def):
        test_store.upsert_job(sample_task_def)
        result = test_store.update_job_status(sample_task_def.name, "paused")
        assert result is True
        job = test_store.get_job(sample_task_def.name)
        assert job.status == "paused"


class TestStateStoreExecutions:
    """执行记录 CRUD 测试"""

    def test_create_execution(self, test_store, sample_task_def):
        test_store.upsert_job(sample_task_def)
        record = ExecutionRecord(
            run_id="test-run-001",
            task_name=sample_task_def.name,
            trigger_type="schedule",
            status=TaskStatus.RUNNING.value,
        )
        exec_id = test_store.create_execution(record)
        assert exec_id > 0

    def test_get_execution(self, test_store, sample_task_def):
        test_store.upsert_job(sample_task_def)
        record = ExecutionRecord(
            run_id="test-run-002",
            task_name=sample_task_def.name,
            status=TaskStatus.RUNNING.value,
        )
        test_store.create_execution(record)
        retrieved = test_store.get_execution("test-run-002")
        assert retrieved is not None
        assert retrieved.run_id == "test-run-002"

    def test_update_execution(self, test_store, sample_task_def):
        test_store.upsert_job(sample_task_def)
        record = ExecutionRecord(
            run_id="test-run-003",
            task_name=sample_task_def.name,
            status=TaskStatus.RUNNING.value,
        )
        test_store.create_execution(record)

        result = test_store.update_execution(
            run_id="test-run-003",
            status=TaskStatus.SUCCESS.value,
            duration=1.5,
        )
        assert result is True

    def test_list_executions(self, test_store, sample_task_def):
        test_store.upsert_job(sample_task_def)
        for i in range(3):
            record = ExecutionRecord(
                run_id=f"test-run-list-{i}",
                task_name=sample_task_def.name,
                status=TaskStatus.SUCCESS.value,
            )
            test_store.create_execution(record)

        records = test_store.list_executions(task_name=sample_task_def.name)
        assert len(records) >= 3


class TestStateStoreIdempotency:
    """幂等键操作测试"""

    def test_record_and_check(self, test_store, sample_task_def):
        test_store.upsert_job(sample_task_def)
        job_id = test_store.upsert_job(sample_task_def)

        result = test_store.record_idempotency(
            job_id=job_id,
            idempotency_key="test_key_001",
            result_data={"status": "ok"},
        )
        assert result is True

        checked = test_store.check_idempotency("test_key_001")
        assert checked is not None
        assert checked["status"] == "ok"

    def test_check_nonexistent_key(self, test_store):
        result = test_store.check_idempotency("nonexistent_key")
        assert result is None

    def test_duplicate_key_fails(self, test_store, sample_task_def):
        job_id = test_store.upsert_job(sample_task_def)
        test_store.record_idempotency(job_id, "dup_key", {"a": 1})
        result = test_store.record_idempotency(job_id, "dup_key", {"b": 2})
        assert result is False  # UNIQUE 约束冲突


class TestStateStoreSchedulerState:
    """调度器状态测试"""

    def test_get_initial_state(self, test_store):
        state = test_store.get_scheduler_state()
        assert state["state"] == "stopped"

    def test_mark_started(self, test_store):
        test_store.mark_started("1.0.0")
        state = test_store.get_scheduler_state()
        assert state["state"] == "running"
        assert state["version"] == "1.0.0"

    def test_heartbeat(self, test_store):
        test_store.mark_started("1.0.0")
        test_store.heartbeat()
        state = test_store.get_scheduler_state()
        assert state["last_heartbeat"] is not None

    def test_update_state_stopped(self, test_store):
        test_store.mark_started("1.0.0")
        test_store.update_scheduler_state("stopped", heartbeat=False)
        state = test_store.get_scheduler_state()
        assert state["state"] == "stopped"
