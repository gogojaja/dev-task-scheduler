"""
config.py 环境变量覆盖 + 配置校验 单元测试
"""

import os
import pytest
from pathlib import Path

from scheduler.config import (
    AppConfig, SchedulerConfig, ExecutionConfig,
    RecordingConfig, AlertingConfig, TaskLoadConfig,
    get_config, set_config,
    apply_env_overrides, validate_config, enforce_validate,
    ConfigValidationError, ENV_PREFIX,
    load_config_from_yaml,
)


# ─── 环境变量覆盖测试 ────────────────────────────────────────

class TestEnvOverridesScheduler:
    """调度器域环境变量覆盖测试"""

    def test_override_timezone(self, monkeypatch):
        monkeypatch.setenv("SCHEDULER_TIMEZONE", "UTC")
        config = AppConfig()
        apply_env_overrides(config)
        assert config.scheduler.timezone == "UTC"

    def test_override_jobstore_path(self, monkeypatch):
        monkeypatch.setenv("SCHEDULER_JOBSTORE_PATH", "/tmp/test.db")
        config = AppConfig()
        apply_env_overrides(config)
        assert config.scheduler.jobstore_path == "/tmp/test.db"

    def test_override_executor_type(self, monkeypatch):
        monkeypatch.setenv("SCHEDULER_EXECUTOR_TYPE", "processpool")
        config = AppConfig()
        apply_env_overrides(config)
        assert config.scheduler.executor_type == "processpool"

    def test_override_max_workers(self, monkeypatch):
        monkeypatch.setenv("SCHEDULER_MAX_WORKERS", "5")
        config = AppConfig()
        apply_env_overrides(config)
        assert config.scheduler.max_workers == 5

    def test_override_max_workers_invalid(self, monkeypatch):
        monkeypatch.setenv("SCHEDULER_MAX_WORKERS", "not_a_number")
        config = AppConfig()
        apply_env_overrides(config)
        # 无效值保持默认
        assert config.scheduler.max_workers == 10

    def test_override_misfire_grace_time(self, monkeypatch):
        monkeypatch.setenv("SCHEDULER_MISFIRE_GRACE_TIME", "1800")
        config = AppConfig()
        apply_env_overrides(config)
        assert config.scheduler.misfire_grace_time == 1800

    def test_override_coalesce_true(self, monkeypatch):
        monkeypatch.setenv("SCHEDULER_COALESCE", "true")
        config = AppConfig()
        config.scheduler.coalesce = False
        apply_env_overrides(config)
        assert config.scheduler.coalesce is True

    def test_override_coalesce_false(self, monkeypatch):
        monkeypatch.setenv("SCHEDULER_COALESCE", "false")
        config = AppConfig()
        apply_env_overrides(config)
        assert config.scheduler.coalesce is False

    def test_override_coalesce_bool_variants(self, monkeypatch):
        """测试各种布尔值格式"""
        for val, expected in [("1", True), ("0", False), ("yes", True), ("no", False), ("on", True), ("off", False)]:
            monkeypatch.setenv("SCHEDULER_COALESCE", val)
            config = AppConfig()
            apply_env_overrides(config)
            assert config.scheduler.coalesce is expected, f"Failed for value {val!r}"


class TestEnvOverridesExecution:
    """执行域环境变量覆盖测试"""

    def test_override_max_retries(self, monkeypatch):
        monkeypatch.setenv("SCHEDULER_MAX_RETRIES", "5")
        config = AppConfig()
        apply_env_overrides(config)
        assert config.execution.default_max_retries == 5

    def test_override_timeout(self, monkeypatch):
        monkeypatch.setenv("SCHEDULER_TIMEOUT", "600")
        config = AppConfig()
        apply_env_overrides(config)
        assert config.execution.default_timeout == 600

    def test_override_retry_base_delay(self, monkeypatch):
        monkeypatch.setenv("SCHEDULER_RETRY_BASE_DELAY", "120")
        config = AppConfig()
        apply_env_overrides(config)
        assert config.execution.retry_base_delay == 120

    def test_override_retry_max_delay(self, monkeypatch):
        monkeypatch.setenv("SCHEDULER_RETRY_MAX_DELAY", "7200")
        config = AppConfig()
        apply_env_overrides(config)
        assert config.execution.retry_max_delay == 7200

    def test_override_retry_jitter(self, monkeypatch):
        monkeypatch.setenv("SCHEDULER_RETRY_JITTER", "60")
        config = AppConfig()
        apply_env_overrides(config)
        assert config.execution.retry_jitter == 60

    def test_override_retry_backoff_factor(self, monkeypatch):
        monkeypatch.setenv("SCHEDULER_RETRY_BACKOFF_FACTOR", "3.0")
        config = AppConfig()
        apply_env_overrides(config)
        assert config.execution.retry_backoff_factor == 3.0

    def test_override_backoff_invalid(self, monkeypatch):
        monkeypatch.setenv("SCHEDULER_RETRY_BACKOFF_FACTOR", "abc")
        config = AppConfig()
        apply_env_overrides(config)
        assert config.execution.retry_backoff_factor == 2.0


class TestEnvOverridesRecording:
    """记录域环境变量覆盖测试"""

    def test_override_recording_enabled(self, monkeypatch):
        monkeypatch.setenv("SCHEDULER_RECORDING_ENABLED", "false")
        config = AppConfig()
        apply_env_overrides(config)
        assert config.recording.enabled is False

    def test_override_csv_path(self, monkeypatch):
        monkeypatch.setenv("SCHEDULER_CSV_PATH", "/tmp/test_record.csv")
        config = AppConfig()
        apply_env_overrides(config)
        assert config.recording.csv_path == "/tmp/test_record.csv"

    def test_override_archive_days(self, monkeypatch):
        monkeypatch.setenv("SCHEDULER_ARCHIVE_DAYS", "30")
        config = AppConfig()
        apply_env_overrides(config)
        assert config.recording.archive_days == 30


class TestEnvOverridesAlerting:
    """告警域环境变量覆盖测试"""

    def test_override_alerting_enabled(self, monkeypatch):
        monkeypatch.setenv("SCHEDULER_ALERTING_ENABLED", "false")
        config = AppConfig()
        apply_env_overrides(config)
        assert config.alerting.enabled is False

    def test_override_system_notification(self, monkeypatch):
        monkeypatch.setenv("SCHEDULER_SYSTEM_NOTIFICATION", "false")
        config = AppConfig()
        apply_env_overrides(config)
        assert config.alerting.system_notification is False

    def test_override_failed_alert_threshold(self, monkeypatch):
        monkeypatch.setenv("SCHEDULER_FAILED_ALERT_THRESHOLD", "3")
        config = AppConfig()
        apply_env_overrides(config)
        assert config.alerting.failed_alert_threshold == 3

    def test_override_queue_alert_threshold(self, monkeypatch):
        monkeypatch.setenv("SCHEDULER_QUEUE_ALERT_THRESHOLD", "100")
        config = AppConfig()
        apply_env_overrides(config)
        assert config.alerting.queue_alert_threshold == 100

    def test_override_heartbeat_timeout(self, monkeypatch):
        monkeypatch.setenv("SCHEDULER_HEARTBEAT_TIMEOUT", "600")
        config = AppConfig()
        apply_env_overrides(config)
        assert config.alerting.heartbeat_timeout == 600

    def test_override_webhook_url(self, monkeypatch):
        monkeypatch.setenv("SCHEDULER_WEBHOOK_URL", "https://hooks.example.com/alert")
        config = AppConfig()
        apply_env_overrides(config)
        assert config.alerting.webhook_url == "https://hooks.example.com/alert"


class TestEnvOverridesTaskLoad:
    """任务加载域环境变量覆盖测试"""

    def test_override_include_paths(self, monkeypatch):
        monkeypatch.setenv("SCHEDULER_INCLUDE_PATHS", "/path/a, /path/b, /path/c")
        config = AppConfig()
        apply_env_overrides(config)
        assert config.task_load.include_paths == ["/path/a", "/path/b", "/path/c"]

    def test_override_config_files(self, monkeypatch):
        monkeypatch.setenv("SCHEDULER_CONFIG_FILES", "a.yaml,b.yaml")
        config = AppConfig()
        apply_env_overrides(config)
        assert config.task_load.config_files == ["a.yaml", "b.yaml"]

    def test_no_override_when_unset(self):
        """环境变量未设置时保持默认值"""
        config = AppConfig()
        original_tz = config.scheduler.timezone
        original_workers = config.scheduler.max_workers
        apply_env_overrides(config)
        assert config.scheduler.timezone == original_tz
        assert config.scheduler.max_workers == original_workers


class TestEnvOverridesGlobal:
    """全局配置环境变量覆盖测试"""

    def test_apply_to_global_config(self, monkeypatch):
        """不传参数时操作全局配置"""
        original = get_config()
        try:
            monkeypatch.setenv("SCHEDULER_TIMEZONE", "UTC")
            result = apply_env_overrides()
            assert result.scheduler.timezone == "UTC"
            assert get_config().scheduler.timezone == "UTC"
        finally:
            set_config(original)

    def test_yaml_then_env_override(self, tmp_path, monkeypatch):
        """YAML 加载后自动应用环境变量覆盖"""
        yaml_content = """
scheduler:
  timezone: UTC
  executor:
    max_workers: 5
"""
        yaml_file = tmp_path / "test_config.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        # 环境变量覆盖 YAML
        monkeypatch.setenv("SCHEDULER_TIMEZONE", "Asia/Shanghai")

        config = load_config_from_yaml(yaml_file)
        # 环境变量优先级高于 YAML
        assert config.scheduler.timezone == "Asia/Shanghai"
        # YAML 值未被环境变量覆盖的保持原样
        assert config.scheduler.max_workers == 5


# ─── 配置校验测试 ────────────────────────────────────────────

class TestValidateConfigScheduler:
    """调度器域配置校验测试"""

    def test_valid_default_config(self):
        config = AppConfig()
        errors = validate_config(config)
        assert errors == []

    def test_invalid_timezone_empty(self):
        config = AppConfig()
        config.scheduler.timezone = ""
        errors = validate_config(config)
        assert any("timezone" in e for e in errors)

    def test_invalid_max_workers_zero(self):
        config = AppConfig()
        config.scheduler.max_workers = 0
        errors = validate_config(config)
        assert any("max_workers" in e for e in errors)

    def test_invalid_max_workers_negative(self):
        config = AppConfig()
        config.scheduler.max_workers = -1
        errors = validate_config(config)
        assert any("max_workers" in e for e in errors)

    def test_invalid_max_workers_too_large(self):
        config = AppConfig()
        config.scheduler.max_workers = 101
        errors = validate_config(config)
        assert any("max_workers" in e for e in errors)

    def test_invalid_misfire_grace_time(self):
        config = AppConfig()
        config.scheduler.misfire_grace_time = -1
        errors = validate_config(config)
        assert any("misfire_grace_time" in e for e in errors)

    def test_invalid_executor_type(self):
        config = AppConfig()
        config.scheduler.executor_type = "invalid"
        errors = validate_config(config)
        assert any("executor_type" in e for e in errors)

    def test_valid_executor_processpool(self):
        config = AppConfig()
        config.scheduler.executor_type = "processpool"
        errors = validate_config(config)
        assert not any("executor_type" in e for e in errors)

    def test_empty_jobstore_path(self):
        config = AppConfig()
        config.scheduler.jobstore_path = ""
        errors = validate_config(config)
        assert any("jobstore_path" in e for e in errors)


class TestValidateConfigExecution:
    """执行域配置校验测试"""

    def test_invalid_max_retries_negative(self):
        config = AppConfig()
        config.execution.default_max_retries = -1
        errors = validate_config(config)
        assert any("max_retries" in e for e in errors)

    def test_invalid_max_retries_too_large(self):
        config = AppConfig()
        config.execution.default_max_retries = 101
        errors = validate_config(config)
        assert any("max_retries" in e for e in errors)

    def test_invalid_timeout_zero(self):
        config = AppConfig()
        config.execution.default_timeout = 0
        errors = validate_config(config)
        assert any("timeout" in e for e in errors)

    def test_invalid_retry_base_delay(self):
        config = AppConfig()
        config.execution.retry_base_delay = -1
        errors = validate_config(config)
        assert any("retry_base_delay" in e for e in errors)

    def test_invalid_retry_max_delay_less_than_base(self):
        config = AppConfig()
        config.execution.retry_base_delay = 100
        config.execution.retry_max_delay = 50
        errors = validate_config(config)
        assert any("retry_max_delay" in e and "retry_base_delay" in e for e in errors)

    def test_valid_retry_max_delay_equal_base(self):
        config = AppConfig()
        config.execution.retry_base_delay = 100
        config.execution.retry_max_delay = 100
        errors = validate_config(config)
        assert not any("retry_max_delay" in e and "retry_base_delay" in e for e in errors)

    def test_invalid_retry_jitter(self):
        config = AppConfig()
        config.execution.retry_jitter = -1
        errors = validate_config(config)
        assert any("retry_jitter" in e for e in errors)

    def test_invalid_backoff_factor(self):
        config = AppConfig()
        config.execution.retry_backoff_factor = 0.5
        errors = validate_config(config)
        assert any("backoff_factor" in e for e in errors)

    def test_valid_backoff_factor_one(self):
        config = AppConfig()
        config.execution.retry_backoff_factor = 1.0
        errors = validate_config(config)
        assert not any("backoff_factor" in e for e in errors)


class TestValidateConfigRecording:
    """记录域配置校验测试"""

    def test_invalid_csv_path_empty(self):
        config = AppConfig()
        config.recording.csv_path = ""
        errors = validate_config(config)
        assert any("csv_path" in e for e in errors)

    def test_invalid_archive_days(self):
        config = AppConfig()
        config.recording.archive_days = 0
        errors = validate_config(config)
        assert any("archive_days" in e for e in errors)


class TestValidateConfigAlerting:
    """告警域配置校验测试"""

    def test_invalid_failed_alert_threshold(self):
        config = AppConfig()
        config.alerting.failed_alert_threshold = 0
        errors = validate_config(config)
        assert any("failed_alert_threshold" in e for e in errors)

    def test_invalid_queue_alert_threshold(self):
        config = AppConfig()
        config.alerting.queue_alert_threshold = 0
        errors = validate_config(config)
        assert any("queue_alert_threshold" in e for e in errors)

    def test_invalid_heartbeat_timeout(self):
        config = AppConfig()
        config.alerting.heartbeat_timeout = 5
        errors = validate_config(config)
        assert any("heartbeat_timeout" in e for e in errors)

    def test_invalid_webhook_url(self):
        config = AppConfig()
        config.alerting.webhook_url = "ftp://invalid.com"
        errors = validate_config(config)
        assert any("webhook_url" in e for e in errors)

    def test_valid_webhook_url_https(self):
        config = AppConfig()
        config.alerting.webhook_url = "https://hooks.example.com/alert"
        errors = validate_config(config)
        assert not any("webhook_url" in e for e in errors)

    def test_valid_webhook_empty(self):
        """空 webhook_url 是合法的（表示禁用）"""
        config = AppConfig()
        config.alerting.webhook_url = ""
        errors = validate_config(config)
        assert not any("webhook_url" in e for e in errors)


class TestEnforceValidate:
    """强制校验测试"""

    def test_valid_config_no_exception(self):
        config = AppConfig()
        enforce_validate(config)  # 不应抛异常

    def test_invalid_config_raises(self):
        config = AppConfig()
        config.scheduler.max_workers = -1
        with pytest.raises(ConfigValidationError) as exc_info:
            enforce_validate(config)
        assert len(exc_info.value.errors) > 0

    def test_multiple_errors_collected(self):
        config = AppConfig()
        config.scheduler.max_workers = -1
        config.execution.default_timeout = 0
        config.alerting.heartbeat_timeout = 1
        with pytest.raises(ConfigValidationError) as exc_info:
            enforce_validate(config)
        assert len(exc_info.value.errors) >= 3

    def test_error_message_format(self):
        config = AppConfig()
        config.scheduler.max_workers = 0
        with pytest.raises(ConfigValidationError) as exc_info:
            enforce_validate(config)
        assert "Config validation failed" in str(exc_info.value)
        assert "max_workers" in str(exc_info.value)

    def test_validate_global_config(self):
        """不传参数时校验全局配置"""
        original = get_config()
        try:
            config = AppConfig()  # 默认配置合法
            set_config(config)
            errors = validate_config()
            assert errors == []
        finally:
            set_config(original)


class TestValidateThenApply:
    """校验与环境变量联合测试"""

    def test_env_fix_invalid_config(self, monkeypatch):
        """配置非法时通过环境变量修复"""
        config = AppConfig()
        config.scheduler.max_workers = -1  # 非法
        errors = validate_config(config)
        assert any("max_workers" in e for e in errors)

        # 通过环境变量修复
        monkeypatch.setenv("SCHEDULER_MAX_WORKERS", "5")
        apply_env_overrides(config)
        errors = validate_config(config)
        assert not any("max_workers" in e for e in errors)
        assert config.scheduler.max_workers == 5

    def test_full_pipeline_yaml_env_validate(self, tmp_path, monkeypatch):
        """完整管线：YAML → 环境变量 → 校验"""
        yaml_content = """
scheduler:
  timezone: UTC
  max_workers: 20
execution:
  default_timeout: 100
"""
        yaml_file = tmp_path / "pipeline_config.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        # 环境变量覆盖部分配置
        monkeypatch.setenv("SCHEDULER_MAX_WORKERS", "8")

        config = load_config_from_yaml(yaml_file)
        # 环境变量优先
        assert config.scheduler.max_workers == 8
        # YAML 值保留
        assert config.execution.default_timeout == 100
        # 校验通过
        errors = validate_config(config)
        assert errors == []
