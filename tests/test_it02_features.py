"""
IT-02 新增功能测试：超时控制 / CSV 轮转查询 / 重试回调
"""

import time
import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from scheduler.timeout import (
    run_with_thread_timeout,
    run_with_smart_timeout,
    get_best_timeout_strategy,
    TimeoutError,
)
from scheduler.retry import (
    RetryPolicy, RetryEvent, RetryCallback,
    LoggingRetryCallback, RetryHistoryEntry,
)
from scheduler.record_writer import RecordWriter
from scheduler.models import ExecutionRecord
from scheduler.config import AppConfig, RecordingConfig, set_config, get_config


# ─── 超时控制测试 ────────────────────────────────────────────

class TestThreadTimeout:
    """线程超时测试"""

    def test_normal_execution(self):
        """正常执行不超时"""
        result = run_with_thread_timeout(lambda: 42, timeout=5)
        assert result == 42

    def test_timeout_raises(self):
        """超时抛出 TimeoutError"""
        def slow_func():
            time.sleep(10)
            return "done"

        with pytest.raises(TimeoutError, match="timed out"):
            run_with_thread_timeout(slow_func, timeout=1)

    def test_exception_propagated(self):
        """函数异常正常传播"""
        def bad_func():
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            run_with_thread_timeout(bad_func, timeout=5)

    def test_with_args(self):
        """带参数执行"""
        def add(a, b):
            return a + b

        result = run_with_thread_timeout(add, args=(3, 4), timeout=5)
        assert result == 7


class TestSmartTimeout:
    """智能超时测试"""

    def test_auto_strategy(self):
        """自动策略选择"""
        strategy = get_best_timeout_strategy()
        assert strategy in ("signal", "thread")

    def test_thread_strategy(self):
        """线程策略"""
        result = run_with_smart_timeout(
            func=lambda: "ok", timeout=5, strategy="thread"
        )
        assert result == "ok"

    def test_thread_strategy_requires_func(self):
        """线程策略需要 func 参数"""
        with pytest.raises(ValueError, match="func"):
            run_with_smart_timeout(func_ref="test:func", strategy="thread")

    def test_process_strategy_requires_func_ref(self):
        """进程策略需要 func_ref 参数"""
        with pytest.raises(ValueError, match="func_ref"):
            run_with_smart_timeout(func=lambda: None, strategy="process")


# ─── CSV 轮转+查询测试 ──────────────────────────────────────

class TestCSVRotation:
    """CSV 轮转测试"""

    @pytest.fixture(autouse=True)
    def _setup_config(self, tmp_path):
        """确保配置正确"""
        original = get_config()
        config = AppConfig()
        config.recording = RecordingConfig(enabled=True, csv_path=str(tmp_path / "test.csv"))
        set_config(config)
        yield
        set_config(original)

    def test_no_rotation_when_small(self, tmp_path):
        """小文件不轮转"""
        csv_path = tmp_path / "test_record.csv"
        writer = RecordWriter(csv_path=csv_path)
        result = writer.rotate_if_needed(max_size_mb=10.0)
        assert result is None

    def test_rotation_when_large(self, tmp_path):
        """大文件触发轮转"""
        csv_path = tmp_path / "test_record.csv"
        writer = RecordWriter(csv_path=csv_path)

        # 写入大量数据使文件超过 1KB
        for i in range(50):
            record = ExecutionRecord(
                run_id=f"RUN-{i}",
                task_name=f"task_{i}",
                trigger_type="schedule",
                status="success",
                duration=1.0,
            )
            writer.append_record(record)

        # 用极小的阈值触发轮转
        result = writer.rotate_if_needed(max_size_mb=0.001)
        assert result is not None
        assert result.exists()

    def test_new_file_after_rotation(self, tmp_path):
        """轮转后新文件存在且含表头"""
        csv_path = tmp_path / "test_record.csv"
        writer = RecordWriter(csv_path=csv_path)

        record = ExecutionRecord(
            run_id="RUN-ROTATE",
            task_name="task",
            trigger_type="schedule",
            status="success",
        )
        writer.append_record(record)

        writer.rotate_if_needed(max_size_mb=0.0001)

        # 新文件应该存在且有表头
        assert csv_path.exists()
        content = csv_path.read_text(encoding="utf-8-sig")
        assert "执行编号" in content


class TestCSVQuery:
    """CSV 查询测试"""

    @pytest.fixture(autouse=True)
    def _setup_config(self, tmp_path):
        """确保配置正确"""
        original = get_config()
        config = AppConfig()
        config.recording = RecordingConfig(enabled=True, csv_path=str(tmp_path / "test.csv"))
        set_config(config)
        yield
        set_config(original)

    def test_query_empty(self, tmp_path):
        """空文件查询返回空列表"""
        csv_path = tmp_path / "test_record.csv"
        writer = RecordWriter(csv_path=csv_path)
        results = writer.query_records()
        assert results == []

    def test_query_all(self, tmp_path):
        """查询所有记录"""
        csv_path = tmp_path / "test_record.csv"
        writer = RecordWriter(csv_path=csv_path)

        for i in range(3):
            record = ExecutionRecord(
                run_id=f"RUN-Q-{i}",
                task_name="test_task",
                trigger_type="schedule",
                status="success",
            )
            writer.append_record(record)

        results = writer.query_records()
        assert len(results) == 3

    def test_query_by_task_name(self, tmp_path):
        """按任务名过滤"""
        csv_path = tmp_path / "test_record.csv"
        writer = RecordWriter(csv_path=csv_path)

        for name in ["task_a", "task_b", "task_a"]:
            record = ExecutionRecord(
                run_id=f"RUN-F-{name}",
                task_name=name,
                trigger_type="schedule",
                status="success",
            )
            writer.append_record(record)

        results = writer.query_records(task_name="task_a")
        assert len(results) == 2

    def test_query_by_status(self, tmp_path):
        """按状态过滤"""
        csv_path = tmp_path / "test_record.csv"
        writer = RecordWriter(csv_path=csv_path)

        for status in ["success", "failed", "success"]:
            record = ExecutionRecord(
                run_id=f"RUN-S-{status}",
                task_name="task",
                trigger_type="schedule",
                status=status,
            )
            writer.append_record(record)

        results = writer.query_records(status="failed")
        assert len(results) == 1

    def test_query_with_limit(self, tmp_path):
        """限制返回数量"""
        csv_path = tmp_path / "test_record.csv"
        writer = RecordWriter(csv_path=csv_path)

        for i in range(10):
            record = ExecutionRecord(
                run_id=f"RUN-L-{i}",
                task_name="task",
                trigger_type="schedule",
                status="success",
            )
            writer.append_record(record)

        results = writer.query_records(limit=3)
        assert len(results) == 3


class TestCSVStatsByDate:
    """按日期统计测试"""

    @pytest.fixture(autouse=True)
    def _setup_config(self, tmp_path):
        """确保配置正确"""
        original = get_config()
        config = AppConfig()
        config.recording = RecordingConfig(enabled=True, csv_path=str(tmp_path / "test.csv"))
        set_config(config)
        yield
        set_config(original)

    def test_stats_by_date(self, tmp_path):
        """按日期统计"""
        csv_path = tmp_path / "test_record.csv"
        writer = RecordWriter(csv_path=csv_path)

        from scheduler.utils import today_str
        stats = writer.get_stats_by_date(today_str())
        assert stats["total"] == 0


# ─── 重试回调+历史测试 ──────────────────────────────────────

class TestRetryCallbacks:
    """重试回调测试"""

    def test_default_callback(self):
        """默认回调存在"""
        policy = RetryPolicy()
        assert len(policy.callbacks) > 0
        assert isinstance(policy.callbacks[0], LoggingRetryCallback)

    def test_custom_callback(self):
        """自定义回调"""
        mock_cb = MagicMock(spec=RetryCallback)
        policy = RetryPolicy(callbacks=[mock_cb])

        policy.notify_retrying("test_task", 0, "error")
        mock_cb.on_retrying.assert_called_once()

    def test_notify_dlq(self):
        """DLQ 通知"""
        mock_cb = MagicMock(spec=RetryCallback)
        policy = RetryPolicy(callbacks=[mock_cb])

        policy.notify_dlq("test_task", 3, "max retries")
        mock_cb.on_dlq.assert_called_once()

    def test_notify_success_after_retry(self):
        """重试后成功通知"""
        mock_cb = MagicMock(spec=RetryCallback)
        policy = RetryPolicy(callbacks=[mock_cb])

        policy.notify_success_after_retry("test_task", 2)
        mock_cb.on_success_after_retry.assert_called_once()

    def test_callback_error_does_not_propagate(self):
        """回调异常不影响主流程"""
        bad_cb = MagicMock(spec=RetryCallback)
        bad_cb.on_retrying.side_effect = RuntimeError("callback error")
        policy = RetryPolicy(callbacks=[bad_cb])

        # 不应抛异常
        policy.notify_retrying("test_task", 0, "error")


class TestRetryHistory:
    """重试历史测试"""

    def test_history_recorded(self):
        """重试事件被记录"""
        policy = RetryPolicy()
        policy.notify_retrying("task_a", 0, "error1")
        policy.notify_retrying("task_a", 1, "error2")
        policy.notify_dlq("task_a", 2, "max retries")

        history = policy.get_history()
        assert len(history) == 3
        assert history[0]["event"] == RetryEvent.RETRYING
        assert history[2]["event"] == RetryEvent.DLQ

    def test_history_filter_by_task(self):
        """按任务名过滤历史"""
        policy = RetryPolicy()
        policy.notify_retrying("task_a", 0, "err")
        policy.notify_retrying("task_b", 0, "err")
        policy.notify_retrying("task_a", 1, "err")

        history = policy.get_history(task_name="task_a")
        assert len(history) == 2
        assert all(h["task_name"] == "task_a" for h in history)

    def test_history_capped(self):
        """历史记录上限控制"""
        policy = RetryPolicy()
        for i in range(1100):
            policy.notify_retrying("task", i % 100, "err")

        assert len(policy.history) <= 600  # 截断到 500

    def test_history_entry_to_dict(self):
        """历史条目序列化"""
        entry = RetryHistoryEntry("task", 1, "retrying", "error", 5.0)
        d = entry.to_dict()
        assert d["task_name"] == "task"
        assert d["retry_count"] == 1
        assert d["event"] == "retrying"
        assert d["error"] == "error"
        assert d["delay"] == 5.0
        assert "timestamp" in d


# ─── 告警通知增强测试 ──────────────────────────────────────

from scheduler.notifier import (
    Notifier, AlertSeverity, ALERT_TEMPLATES, _render_template,
)
from scheduler.config import AppConfig, AlertingConfig, set_config, get_config


class TestAlertTemplates:
    """告警模板测试"""

    def test_render_task_failed(self):
        """任务失败模板渲染"""
        title, msg = _render_template(
            "task_failed",
            task_name="backup",
            error_message="disk full",
            run_id="RUN-001",
            retry_count=1,
        )
        assert "backup" in title
        assert "disk full" in msg
        assert "RUN-001" in msg
        assert "第 2 次" in msg  # retry_count+1

    def test_render_dlq(self):
        """死信队列模板渲染"""
        title, msg = _render_template(
            "task_dlq",
            task_name="import",
            error_message="timeout",
            run_id="RUN-002",
        )
        assert "死信队列" in title
        assert "import" in title

    def test_render_unknown_template(self):
        """未知模板降级"""
        title, msg = _render_template("unknown_type", foo="bar")
        assert "unknown_type" in title
        assert "bar" in msg

    def test_error_truncation(self):
        """错误信息自动截断"""
        long_err = "x" * 500
        _, msg = _render_template(
            "task_failed",
            task_name="t",
            error_message=long_err,
            run_id="",
            retry_count=0,
        )
        assert len(msg) < 500

    def test_severity_levels(self):
        """严重级别定义"""
        assert AlertSeverity.INFO == "info"
        assert AlertSeverity.WARNING == "warning"
        assert AlertSeverity.CRITICAL == "critical"


class TestNotifierEnhancements:
    """通知器增强测试"""

    @pytest.fixture(autouse=True)
    def _setup_config(self, tmp_path):
        """确保告警配置正确"""
        original = get_config()
        config = AppConfig()
        config.alerting = AlertingConfig(
            enabled=True,
            system_notification=False,
            webhook_url="",
        )
        set_config(config)
        yield
        set_config(original)

    def test_stats_initialized(self):
        """统计初始化"""
        n = Notifier()
        stats = n.get_alert_stats()
        assert "total_sent" in stats
        assert "suppressed" in stats

    def test_suppressed_counted(self):
        """被抑制的告警计入统计"""
        n = Notifier()
        n._min_alert_interval = 999999
        # 第一次发送成功，第二次被抑制
        n.alert_task_failed("task_a", "err", run_id="R1")
        n.alert_task_failed("task_a", "err2", run_id="R2")
        stats = n.get_alert_stats()
        assert stats["suppressed"] >= 1

    def test_all_alert_methods(self):
        """所有告警方法可用"""
        n = Notifier()
        n.alert_task_failed("t", "err")
        n.alert_task_dlq("t", "err")
        n.alert_queue_buildup(100, 50)
        n.alert_heartbeat_lost("never")
        stats = n.get_alert_stats()
        assert stats["total_sent"] >= 4

    def test_disabled_no_send(self):
        """禁用时静默"""
        original = get_config()
        config = AppConfig()
        config.alerting = AlertingConfig(enabled=False)
        set_config(config)
        try:
            n = Notifier()
            n.alert_task_failed("t", "err")
            stats = n.get_alert_stats()
            assert stats["total_sent"] == 0
        finally:
            set_config(original)


# ─── 跨项目上下文测试 ──────────────────────────────────────

from scheduler.context import SchedulerContext, get_context_info
from scheduler.utils import (
    set_project_root, get_project_root, reset_project_root,
)


class TestProjectRoot:
    """项目根目录测试"""

    def test_default_root(self):
        """默认项目根"""
        reset_project_root()
        root = get_project_root()
        assert root.is_dir()
        assert (root / "scheduler").is_dir()

    def test_set_project_root(self):
        """编程式设置项目根"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            set_project_root(tmp)
            root = get_project_root()
            assert root == Path(tmp).resolve()
        reset_project_root()

    def test_env_project_root(self, monkeypatch):
        """环境变量设置项目根"""
        import tempfile
        reset_project_root()
        with tempfile.TemporaryDirectory() as tmp:
            monkeypatch.setenv("SCHEDULER_PROJECT_ROOT", tmp)
            root = get_project_root()
            assert root == Path(tmp).resolve()

    def test_invalid_env_ignored(self, monkeypatch):
        """无效环境变量被忽略"""
        reset_project_root()
        monkeypatch.setenv("SCHEDULER_PROJECT_ROOT", "/nonexistent/path")
        root = get_project_root()
        assert (root / "scheduler").is_dir()

    def test_reset(self):
        """重置项目根"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            set_project_root(tmp)
            assert get_project_root() == Path(tmp).resolve()
        reset_project_root()
        assert (get_project_root() / "scheduler").is_dir()


class TestSchedulerContext:
    """调度器上下文测试"""

    def test_default_context(self):
        """默认上下文"""
        ctx = SchedulerContext()
        assert ctx.project_root.is_dir()
        assert not ctx.is_initialized

    def test_explicit_project_root(self, tmp_path):
        """显式指定项目根"""
        ctx = SchedulerContext(project_root=tmp_path)
        assert ctx.project_root == tmp_path.resolve()

    def test_initialize(self):
        """初始化上下文"""
        ctx = SchedulerContext()
        ctx.initialize()
        assert ctx.is_initialized
        assert ctx.config is not None
        ctx.shutdown()

    def test_get_info(self):
        """获取上下文信息"""
        ctx = SchedulerContext()
        ctx.initialize()
        info = ctx.get_info()
        assert "project_root" in info
        assert "timezone" in info
        assert info["initialized"] is True
        ctx.shutdown()

    def test_context_info(self):
        """获取全局上下文信息"""
        info = get_context_info()
        assert "project_root" in info
        assert "timezone" in info

    def test_create_scheduler_requires_init(self):
        """未初始化创建调度器抛异常"""
        ctx = SchedulerContext()
        with pytest.raises(RuntimeError, match="not initialized"):
            ctx.create_scheduler()
