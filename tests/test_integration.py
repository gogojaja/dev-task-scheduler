"""
集成测试：端到端流程验证

测试配置加载 → 任务注册 → 执行 → 记录 全链路。
"""

import pytest
from pathlib import Path

from scheduler.config import (
    AppConfig, SchedulerConfig, ExecutionConfig,
    RecordingConfig, AlertingConfig, set_config, get_config,
)
from scheduler.registry import TaskRegistry
from scheduler.idempotency import IdempotencyManager
from scheduler.retry import RetryPolicy
from scheduler.state_store import StateStore
from scheduler.record_writer import RecordWriter
from scheduler.executor import TaskExecutor
from scheduler.models import TaskResult, TaskContext, TaskDefinition, TriggerType


@pytest.fixture()
def integration_env(tmp_path):
    """创建完整集成测试环境"""
    db_path = tmp_path / "test.db"
    csv_path = tmp_path / "records.csv"

    original = get_config()

    config = AppConfig(
        scheduler=SchedulerConfig(
            timezone="Asia/Shanghai",
            jobstore_path=str(db_path),
            max_workers=2,
        ),
        execution=ExecutionConfig(
            default_max_retries=2,
            default_timeout=10,
            retry_base_delay=1,
            retry_max_delay=5,
            retry_jitter=0,
            retry_backoff_factor=2.0,
        ),
        recording=RecordingConfig(
            enabled=True,
            csv_path=str(csv_path),
        ),
        alerting=AlertingConfig(enabled=False),
    )
    set_config(config)

    store = StateStore()
    registry = TaskRegistry()
    idempotency = IdempotencyManager()
    retry_policy = RetryPolicy.from_config()
    record_writer = RecordWriter(csv_path=csv_path)

    yield {
        "config": config,
        "store": store,
        "registry": registry,
        "idempotency": idempotency,
        "retry_policy": retry_policy,
        "record_writer": record_writer,
        "db_path": db_path,
        "csv_path": csv_path,
    }

    store.close()
    set_config(original)


class TestIntegrationEndToEnd:
    """端到端集成测试"""

    def test_task_registration_and_listing(self, integration_env):
        """任务注册后可列出"""
        env = integration_env
        registry = env["registry"]

        task_def = TaskDefinition(
            name="integ_task_1",
            func_ref="tests.test_helpers:sample_func",
            trigger_type=TriggerType.CRON,
            trigger_config={"hour": 2, "minute": 0},
            description="集成测试任务",
        )
        registry.register(task_def)

        tasks = registry.list_all()
        assert len(tasks) >= 1
        assert any(t.name == "integ_task_1" for t in tasks)

    def test_idempotency_check_flow(self, integration_env):
        """幂等校验流程：不存在的键返回 None"""
        env = integration_env
        idem = env["idempotency"]

        import uuid, time
        unique_key = f"idem_{uuid.uuid4().hex[:8]}_{int(time.time())}"

        # 不存在的键返回 None
        result = idem.check(unique_key)
        assert result is None

        # check_or_skip 也返回 (False, None)
        should_skip, data = idem.check_or_skip(unique_key)
        assert should_skip is False
        assert data is None

    def test_retry_policy_delay(self, integration_env):
        """重试延迟计算"""
        env = integration_env
        policy = env["retry_policy"]

        # 第 0 次重试：base_delay * (factor ^ 0) = 1 * 1 = 1
        delay0 = policy.calculate_delay(0)
        assert delay0 >= 1.0

        # 第 1 次重试：base_delay * (factor ^ 1) = 1 * 2 = 2
        delay1 = policy.calculate_delay(1)
        assert delay1 >= 2.0

        # 不超过 max_delay
        for i in range(10):
            delay = policy.calculate_delay(i)
            assert delay <= policy.max_delay + policy.jitter

    def test_state_store_job_lifecycle(self, integration_env):
        """任务定义生命周期：创建 → 查询 → 删除"""
        env = integration_env
        store = env["store"]

        task_def = TaskDefinition(
            name="lifecycle_task",
            func_ref="tests.test_helpers:sample_func",
            trigger_type=TriggerType.CRON,
            trigger_config={"hour": 1},
        )
        job_id = store.upsert_job(task_def)
        assert job_id > 0

        # 查询
        job = store.get_job("lifecycle_task")
        assert job is not None
        assert job.name == "lifecycle_task"

        # 删除
        deleted = store.delete_job("lifecycle_task")
        assert deleted is True

        # 确认删除
        job = store.get_job("lifecycle_task")
        assert job is None

    def test_record_writer_end_to_end(self, integration_env):
        """记录写入器端到端"""
        env = integration_env
        writer = env["record_writer"]

        from scheduler.models import ExecutionRecord
        record = ExecutionRecord(
            run_id="RUN-INTEG-001",
            task_name="integ_task",
            trigger_type="cron",
            status="success",
            duration=5.2,
        )

        exec_no = writer.append_record(record)
        assert exec_no is not None

        # 查询
        results = writer.query_records(task_name="integ_task")
        assert len(results) == 1
        assert results[0]["任务名称"] == "integ_task"

    def test_executor_with_mock_task(self, integration_env):
        """执行器执行模拟任务"""
        env = integration_env

        executor = TaskExecutor(
            store=env["store"],
            record_writer=env["record_writer"],
            idempotency_mgr=env["idempotency"],
            retry_policy=env["retry_policy"],
        )

        task_def = TaskDefinition(
            name="exec_test",
            func_ref="tests.test_helpers:sample_func",
            timeout=5,
            idempotency_key_expr="{run_id}",
        )

        result = executor.execute_task(task_def, trigger_type="manual")

        assert isinstance(result, TaskResult)
        assert result.success is True
