"""
测试基础设施 — pytest 全局 fixture

提供临时数据库、配置、注册表等 fixture，
保证测试间隔离，不污染生产数据。
"""

from __future__ import annotations

import os
import sys
import tempfile
import shutil
from pathlib import Path
from typing import Generator

import pytest

# 确保项目根目录在 sys.path 中
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


@pytest.fixture()
def tmp_dir(tmp_path: Path) -> Path:
    """提供临时目录，测试结束自动清理"""
    return tmp_path


@pytest.fixture()
def tmp_db(tmp_path: Path) -> Path:
    """提供临时 SQLite 数据库路径"""
    db_path = tmp_path / "test_scheduler.db"
    return db_path


@pytest.fixture()
def tmp_csv(tmp_path: Path) -> Path:
    """提供临时 CSV 台账路径"""
    csv_dir = tmp_path / "台账"
    csv_dir.mkdir()
    return csv_dir / "test_execution_record.csv"


@pytest.fixture()
def test_config(tmp_db: Path, tmp_csv: Path):
    """提供测试用 AppConfig，路径指向临时文件

    自动替换全局配置，测试结束恢复。
    """
    from scheduler.config import (
        AppConfig, SchedulerConfig, ExecutionConfig,
        RecordingConfig, AlertingConfig, TaskLoadConfig,
        set_config, get_config,
    )

    # 保存原始配置
    original_config = get_config()

    # 创建测试配置
    config = AppConfig(
        scheduler=SchedulerConfig(
            timezone="Asia/Shanghai",
            jobstore_path=str(tmp_db),
            executor_type="threadpool",
            max_workers=2,
            misfire_grace_time=60,
            coalesce=True,
        ),
        execution=ExecutionConfig(
            default_max_retries=2,
            default_timeout=10,
            retry_base_delay=1,
            retry_max_delay=10,
            retry_jitter=0,
            retry_backoff_factor=2.0,
        ),
        recording=RecordingConfig(
            enabled=True,
            csv_path=str(tmp_csv),
            archive_days=7,
        ),
        alerting=AlertingConfig(
            enabled=False,  # 测试时禁用告警
            system_notification=False,
            webhook_url="",
        ),
        task_load=TaskLoadConfig(),
    )

    set_config(config)
    yield config

    # 恢复原始配置
    set_config(original_config)


@pytest.fixture()
def test_store(tmp_db: Path, test_config):
    """提供测试用 StateStore 实例

    使用临时数据库，测试结束自动关闭连接。
    """
    from scheduler.state_store import StateStore

    store = StateStore(db_path=tmp_db)
    yield store
    store.close()


@pytest.fixture()
def test_registry(test_store):
    """提供测试用 TaskRegistry 实例

    使用测试数据库，不污染全局注册表。
    """
    from scheduler.registry import TaskRegistry

    registry = TaskRegistry()
    # 替换内部 store 为测试 store
    registry._store = test_store
    yield registry


@pytest.fixture()
def test_idempotency(test_store):
    """提供测试用 IdempotencyManager"""
    from scheduler.idempotency import IdempotencyManager

    return IdempotencyManager(store=test_store)


@pytest.fixture()
def test_retry_policy(test_config):
    """提供测试用 RetryPolicy"""
    from scheduler.retry import RetryPolicy

    return RetryPolicy(
        max_retries=2,
        base_delay=1,
        max_delay=10,
        jitter=0,
        backoff_factor=2.0,
    )


@pytest.fixture()
def test_record_writer(tmp_csv: Path, test_config):
    """提供测试用 RecordWriter"""
    from scheduler.record_writer import RecordWriter

    return RecordWriter(csv_path=tmp_csv)


@pytest.fixture()
def test_notifier(test_config):
    """提供测试用 Notifier（告警已禁用）"""
    from scheduler.notifier import Notifier

    return Notifier()


@pytest.fixture()
def test_executor(test_store, test_record_writer, test_idempotency, test_retry_policy):
    """提供测试用 TaskExecutor"""
    from scheduler.executor import TaskExecutor

    return TaskExecutor(
        store=test_store,
        record_writer=test_record_writer,
        idempotency_mgr=test_idempotency,
        retry_policy=test_retry_policy,
    )


@pytest.fixture()
def sample_task_def():
    """提供示例 TaskDefinition"""
    from scheduler.models import TaskDefinition, TriggerType

    return TaskDefinition(
        name="test_task",
        func_ref="tests.test_helpers:sample_func",
        trigger_type=TriggerType.CRON,
        trigger_config={"minute": "0", "hour": "2"},
        description="测试任务",
        max_retries=2,
        timeout=10,
        idempotency_key_expr="{date}",
        status="active",
    )


@pytest.fixture()
def sample_task_def_interval():
    """提供 interval 类型示例 TaskDefinition"""
    from scheduler.models import TaskDefinition, TriggerType

    return TaskDefinition(
        name="test_task_interval",
        func_ref="tests.test_helpers:sample_func",
        trigger_type=TriggerType.INTERVAL,
        trigger_config={"minutes": 5},
        description="测试 interval 任务",
        max_retries=2,
        timeout=10,
        idempotency_key_expr="{datetime}",
        status="active",
    )


@pytest.fixture()
def sample_task_def_date():
    """提供 date 类型示例 TaskDefinition"""
    from scheduler.models import TaskDefinition, TriggerType

    return TaskDefinition(
        name="test_task_date",
        func_ref="tests.test_helpers:sample_func",
        trigger_type=TriggerType.DATE,
        trigger_config={"run_date": "2030-01-01 00:00:00"},
        description="测试 date 任务",
        max_retries=1,
        timeout=10,
        idempotency_key_expr="{run_id}",
        status="active",
    )
