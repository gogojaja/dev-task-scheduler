"""
补充覆盖率测试：notifier / record_writer / timeout / registry / context
"""

import json
import time
import tempfile
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scheduler.config import (
    AppConfig, AlertingConfig, RecordingConfig, set_config, get_config,
)
from scheduler.notifier import Notifier, _render_template
from scheduler.record_writer import RecordWriter
from scheduler.models import ExecutionRecord


# ─── Notifier 覆盖率补充 ─────────────────────────────────────

class TestNotifierCoverage:
    """补充通知器覆盖率"""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        original = get_config()
        config = AppConfig()
        config.alerting = AlertingConfig(enabled=True, system_notification=False, webhook_url="")
        set_config(config)
        yield
        set_config(original)

    def test_should_alert_first_time(self):
        """首次告警总是通过"""
        n = Notifier()
        assert n._should_alert("test_key") is True

    def test_should_alert_suppressed(self):
        """短时间内重复告警被抑制"""
        n = Notifier()
        n._min_alert_interval = 999999
        n._should_alert("test_key")
        assert n._should_alert("test_key") is False

    def test_system_notification_disabled(self):
        """系统通知禁用时静默"""
        n = Notifier()
        n.system_notification = False
        n._send_system_notification("title", "msg")  # 不应抛异常

    def test_webhook_empty(self):
        """Webhook URL 为空时静默"""
        n = Notifier()
        n.webhook_url = ""
        n._send_webhook_with_retry("test", "info", {})  # 不应抛异常

    def test_alert_stats_keys(self):
        """统计包含所有键"""
        n = Notifier()
        stats = n.get_alert_stats()
        expected_keys = {"total_sent", "system_sent", "webhook_sent", "webhook_failed", "suppressed"}
        assert expected_keys.issubset(stats.keys())


# ─── RecordWriter 覆盖率补充 ──────────────────────────────────

class TestRecordWriterCoverage:
    """补充记录写入器覆盖率"""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        original = get_config()
        config = AppConfig()
        config.recording = RecordingConfig(enabled=True, csv_path=str(tmp_path / "test.csv"))
        set_config(config)
        yield
        set_config(original)

    def test_record_count(self, tmp_path):
        """记录计数"""
        csv_path = tmp_path / "test.csv"
        writer = RecordWriter(csv_path=csv_path)
        assert writer.get_record_count() == 0

        record = ExecutionRecord(
            run_id="RUN-COUNT", task_name="t", trigger_type="cron", status="success",
        )
        writer.append_record(record)
        assert writer.get_record_count() == 1

    def test_batch_append(self, tmp_path):
        """批量追加"""
        csv_path = tmp_path / "test.csv"
        writer = RecordWriter(csv_path=csv_path)

        records = [
            ExecutionRecord(run_id=f"RUN-B{i}", task_name="t", trigger_type="cron", status="success")
            for i in range(5)
        ]
        exec_nos = writer.append_batch(records)
        assert len(exec_nos) == 5
        assert writer.get_record_count() == 5

    def test_list_archives_empty(self, tmp_path):
        """无归档文件时返回空列表"""
        csv_path = tmp_path / "test.csv"
        writer = RecordWriter(csv_path=csv_path)
        archives = writer.list_archives()
        assert archives == []

    def test_rotation_creates_archive(self, tmp_path):
        """轮转后可列出归档"""
        csv_path = tmp_path / "test.csv"
        writer = RecordWriter(csv_path=csv_path)

        # 写入数据
        for i in range(10):
            writer.append_record(ExecutionRecord(
                run_id=f"RUN-R{i}", task_name="t", trigger_type="cron", status="success",
            ))

        # 轮转
        archive = writer.rotate_if_needed(max_size_mb=0.0001)
        assert archive is not None

        # 列出归档
        archives = writer.list_archives()
        assert len(archives) >= 1

    def test_get_today_stats_empty(self, tmp_path):
        """空文件今日统计"""
        csv_path = tmp_path / "test.csv"
        writer = RecordWriter(csv_path=csv_path)
        stats = writer.get_today_stats()
        assert stats["total"] == 0

    def test_disabled_writer(self, tmp_path):
        """禁用时写入静默"""
        original = get_config()
        config = AppConfig()
        config.recording = RecordingConfig(enabled=False, csv_path=str(tmp_path / "test.csv"))
        set_config(config)
        try:
            writer = RecordWriter(csv_path=tmp_path / "test.csv")
            record = ExecutionRecord(run_id="RUN-D", task_name="t", trigger_type="cron", status="success")
            result = writer.append_record(record)
            assert result is None
        finally:
            set_config(original)

    def test_record_to_row_fields(self, tmp_path):
        """行字段完整性"""
        csv_path = tmp_path / "test.csv"
        writer = RecordWriter(csv_path=csv_path)
        record = ExecutionRecord(
            run_id="RUN-FIELDS", task_name="my_task", trigger_type="cron",
            status="success", duration=1.5, retry_count=2, idempotency_key="key123",
        )
        row = writer._record_to_row(record, "EX-TEST")
        assert len(row) == 15
        assert row[0] == "EX-TEST"
        assert row[1] == "my_task"
        assert row[8] == "success"
        assert row[9] == "2"


# ─── Timeout 覆盖率补充 ──────────────────────────────────────

class TestTimeoutCoverage:
    """补充超时控制覆盖率"""

    def test_process_timeout(self):
        """进程级超时：subprocess 超时场景"""
        from scheduler.timeout import run_with_process_timeout

        # 进程内函数抛异常时，抛 RuntimeError（非 TimeoutError）
        with pytest.raises(RuntimeError, match="Subprocess failed"):
            run_with_process_timeout(
                "tests.test_helpers:sample_func_fail",
                args=(),
                timeout=10,
            )

    def test_smart_timeout_process(self):
        """智能超时选择进程策略"""
        from scheduler.timeout import run_with_smart_timeout

        with pytest.raises(RuntimeError, match="Subprocess failed"):
            run_with_smart_timeout(
                func_ref="tests.test_helpers:sample_func_fail",
                args=(),
                timeout=10,
                strategy="process",
            )

    def test_smart_timeout_auto_process(self):
        """自动策略在非 Windows 上选择进程"""
        from scheduler.timeout import run_with_smart_timeout

        with pytest.raises(RuntimeError, match="Subprocess failed"):
            run_with_smart_timeout(
                func_ref="tests.test_helpers:sample_func_fail",
                args=(),
                timeout=10,
                strategy="auto",
            )


# ─── Registry 覆盖率补充 ──────────────────────────────────────

class TestRegistryCoverage:
    """补充注册表覆盖率"""

    def test_register_and_get(self):
        """注册并获取任务"""
        from scheduler.registry import TaskRegistry
        from scheduler.models import TaskDefinition, TriggerType

        registry = TaskRegistry()
        task = TaskDefinition(
            name="cov_test_task",
            func_ref="tests.test_helpers:sample_func",
            trigger_type=TriggerType.CRON,
        )
        registry.register(task)

        found = registry.get("cov_test_task")
        assert found is not None
        assert found.name == "cov_test_task"

    def test_register_duplicate_overwrites(self):
        """重复注册覆盖（带警告）"""
        from scheduler.registry import TaskRegistry
        from scheduler.models import TaskDefinition, TriggerType

        registry = TaskRegistry()
        task = TaskDefinition(
            name="dup_task",
            func_ref="tests.test_helpers:sample_func",
            trigger_type=TriggerType.CRON,
        )
        registry.register(task)
        registry.register(task)  # 覆盖，不抛异常
        assert registry.get("dup_task") is not None

    def test_get_nonexistent(self):
        """获取不存在的任务返回 None"""
        from scheduler.registry import TaskRegistry
        registry = TaskRegistry()
        assert registry.get("nonexistent") is None
