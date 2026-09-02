"""
config.py + retry.py + utils.py 单元测试
"""

import pytest
from pathlib import Path

from scheduler.config import (
    AppConfig, SchedulerConfig, ExecutionConfig,
    RecordingConfig, AlertingConfig, get_config, set_config,
    load_config_from_yaml, resolve_path, ensure_storage_dirs,
)
from scheduler.retry import RetryPolicy
from scheduler.utils import (
    now, now_str, today_str, format_datetime, parse_datetime,
    gen_uuid, gen_execution_id, get_node_id,
    get_os, is_macos, get_project_root,
    safe_truncate, is_blank, load_function,
)


class TestAppConfig:
    """配置数据类测试"""

    def test_default_config(self):
        config = AppConfig()
        assert config.scheduler.timezone == "Asia/Shanghai"
        assert config.scheduler.max_workers == 10
        assert config.execution.default_max_retries == 3
        assert config.execution.default_timeout == 300
        assert config.recording.enabled is True
        assert config.alerting.enabled is True

    def test_scheduler_config_defaults(self):
        sc = SchedulerConfig()
        assert sc.misfire_grace_time == 3600
        assert sc.coalesce is True

    def test_execution_config_defaults(self):
        ec = ExecutionConfig()
        assert ec.retry_base_delay == 60
        assert ec.retry_max_delay == 3600
        assert ec.retry_jitter == 30
        assert ec.retry_backoff_factor == 2.0


class TestConfigYaml:
    """YAML 配置加载测试"""

    def test_load_nonexistent_yaml(self, tmp_path):
        """不存在的 YAML 返回默认配置"""
        from scheduler.config import AppConfig, set_config
        # 重置全局配置为默认值，避免其他测试污染
        set_config(AppConfig())
        config = load_config_from_yaml(tmp_path / "nonexistent.yaml")
        assert config.scheduler.timezone == "Asia/Shanghai"

    def test_load_valid_yaml(self, tmp_path):
        """加载有效 YAML"""
        yaml_content = """
scheduler:
  timezone: UTC
  misfire_grace_time: 1800
execution:
  default_max_retries: 5
  default_timeout: 600
recording:
  enabled: false
alerting:
  enabled: false
"""
        yaml_file = tmp_path / "test_config.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        config = load_config_from_yaml(yaml_file)
        assert config.scheduler.timezone == "UTC"
        assert config.scheduler.misfire_grace_time == 1800
        assert config.execution.default_max_retries == 5
        assert config.execution.default_timeout == 600
        assert config.recording.enabled is False
        assert config.alerting.enabled is False


class TestRetryPolicy:
    """重试策略测试"""

    def test_calculate_delay_first_retry(self):
        policy = RetryPolicy(base_delay=60, backoff_factor=2.0, jitter=0)
        delay = policy.calculate_delay(0)
        assert delay == 60.0

    def test_calculate_delay_exponential(self):
        policy = RetryPolicy(base_delay=60, backoff_factor=2.0, jitter=0)
        delay_0 = policy.calculate_delay(0)
        delay_1 = policy.calculate_delay(1)
        delay_2 = policy.calculate_delay(2)
        assert delay_1 == 120.0
        assert delay_2 == 240.0

    def test_calculate_delay_max_cap(self):
        policy = RetryPolicy(base_delay=60, max_delay=100, backoff_factor=2.0, jitter=0)
        delay = policy.calculate_delay(10)
        assert delay == 100.0

    def test_calculate_delay_with_jitter(self):
        policy = RetryPolicy(base_delay=60, backoff_factor=2.0, jitter=30)
        delay = policy.calculate_delay(0)
        assert 60.0 <= delay <= 90.0

    def test_should_retry_normal(self):
        policy = RetryPolicy(max_retries=3)
        assert policy.should_retry(0) is True
        assert policy.should_retry(2) is True
        assert policy.should_retry(3) is False

    def test_should_retry_skip(self):
        policy = RetryPolicy(max_retries=3)
        assert policy.should_retry(0, skip_retry=True) is False

    def test_is_dead_letter_max_retries(self):
        policy = RetryPolicy(max_retries=3)
        assert policy.is_dead_letter(3) is True
        assert policy.is_dead_letter(2) is False

    def test_is_dead_letter_skip_retry(self):
        policy = RetryPolicy(max_retries=3)
        assert policy.is_dead_letter(0, skip_retry=True) is True


class TestUtilsTime:
    """时间工具函数测试"""

    def test_now_has_timezone(self):
        dt = now()
        assert dt.tzinfo is not None

    def test_today_str_format(self):
        s = today_str()
        assert len(s) == 10
        assert s[4] == "-"

    def test_format_datetime_none(self):
        assert format_datetime(None) == ""

    def test_format_datetime_with_value(self):
        dt = now()
        s = format_datetime(dt)
        assert len(s) > 0

    def test_parse_datetime_valid(self):
        dt = parse_datetime("2026-09-02 14:00:00")
        assert dt is not None
        assert dt.year == 2026

    def test_parse_datetime_invalid(self):
        assert parse_datetime("invalid") is None
        assert parse_datetime("") is None


class TestUtilsId:
    """ID 生成工具测试"""

    def test_gen_uuid_format(self):
        uid = gen_uuid()
        assert len(uid) == 36
        assert "-" in uid

    def test_gen_execution_id_format(self):
        eid = gen_execution_id()
        assert eid.startswith("EX-")

    def test_gen_uuid_unique(self):
        ids = {gen_uuid() for _ in range(100)}
        assert len(ids) == 100


class TestUtilsPlatform:
    """平台检测工具测试"""

    def test_get_os(self):
        os_type = get_os()
        assert os_type in ("macos", "linux", "windows")

    def test_get_project_root(self):
        root = get_project_root()
        assert root.exists()
        assert (root / "scheduler").exists()


class TestUtilsString:
    """字符串工具测试"""

    def test_safe_truncate_short(self):
        assert safe_truncate("hello", 10) == "hello"

    def test_safe_truncate_long(self):
        result = safe_truncate("a" * 600, 500)
        assert len(result) == 500
        assert result.endswith("...")

    def test_safe_truncate_none(self):
        assert safe_truncate(None) is None

    def test_is_blank(self):
        assert is_blank("") is True
        assert is_blank(None) is True
        assert is_blank("  ") is True
        assert is_blank("hello") is False


class TestLoadFunction:
    """函数加载测试"""

    def test_load_valid_function(self):
        func = load_function("tests.test_helpers:sample_func")
        assert callable(func)
        assert func() == "ok"

    def test_load_invalid_format(self):
        with pytest.raises(ValueError):
            load_function("no_colon_here")

    def test_load_invalid_module(self):
        with pytest.raises(ImportError):
            load_function("nonexistent_module:func")

    def test_load_invalid_function(self):
        with pytest.raises(AttributeError):
            load_function("tests.test_helpers:nonexistent_func")
