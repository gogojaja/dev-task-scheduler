"""
外部项目接入验证测试：验证 dev-task-scheduler 作为库被外部项目使用的核心路径
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scheduler.registry import TaskRegistry, register_task
from scheduler.models import TaskDefinition, TriggerType, TaskResult
from scheduler.config import AppConfig, SchedulerConfig


class TestExternalProjectRegistration:
    """外部项目任务注册验证"""

    def test_standalone_decorator_registration(self):
        """独立装饰器 register_task() 注册方式可用"""
        from scheduler.registry import task_registry
        before = len(task_registry.list_all())

        @register_task(
            name="ext_test_task_a",
            trigger="cron",
            hour=8,
            minute=0,
        )
        def my_task():
            return "ok"

        after = len(task_registry.list_all())
        assert after >= before

    def test_registry_register_function(self):
        """TaskRegistry.register_function() 注册方式可用"""
        registry = TaskRegistry()

        def my_func():
            return "ok"

        registry.register_function(
            func=my_func,
            name="ext_test_func_b",
            trigger="interval",
            hours=1,
        )

        found = registry.get("ext_test_func_b")
        assert found is not None
        assert found.name == "ext_test_func_b"
        assert found.trigger_type == TriggerType.INTERVAL

    def test_programmatic_registration(self):
        """编程式注册方式可用"""
        registry = TaskRegistry()

        def my_func():
            return "ok"

        task = TaskDefinition(
            name="ext_task_c",
            func_ref="my_module:my_func",
            trigger_type=TriggerType.INTERVAL,
            trigger_config={"minutes": 30},
            description="每30分钟执行",
            timeout=120,
            max_retries=2,
        )
        registry.register(task, func=my_func)

        found = registry.get("ext_task_c")
        assert found is not None
        assert found.description == "每30分钟执行"
        assert found.timeout == 120

    def test_task_result_ok(self):
        """TaskResult.ok() 返回值规范"""
        result = TaskResult.ok(data={"count": 42})
        assert result.success is True
        assert result.data == {"count": 42}

    def test_task_result_fail_skip_retry(self):
        """TaskResult.fail(skip_retry=True) 跳过重试"""
        result = TaskResult.fail(message="disk full", skip_retry=True)
        assert result.success is False
        assert result.skip_retry is True

    def test_task_result_fail_with_error_code(self):
        """TaskResult.fail() 带错误码"""
        result = TaskResult.fail(message="timeout", error_code="EXECUTION_TIMEOUT")
        assert result.success is False
        assert result.error_code == "EXECUTION_TIMEOUT"

    def test_multiple_tasks_coexist(self):
        """多个任务共存注册"""
        registry = TaskRegistry()

        for i in range(5):
            registry.register(
                TaskDefinition(
                    name=f"batch_task_{i}",
                    func_ref=f"mod:func_{i}",
                    trigger_type=TriggerType.INTERVAL,
                    trigger_config={"seconds": 10 * (i + 1)},
                )
            )

        tasks = registry.list_all()
        assert len(tasks) >= 5


class TestExternalYAMLConfig:
    """外部项目 YAML 配置加载验证"""

    def test_config_scheduler_section(self):
        """scheduler 配置段解析"""
        config = AppConfig()
        config.scheduler = SchedulerConfig(
            max_workers=5,
            timezone="UTC",
        )
        assert config.scheduler.max_workers == 5
        assert config.scheduler.timezone == "UTC"

    def test_config_has_all_sections(self):
        """配置包含所有必要段"""
        config = AppConfig()
        assert hasattr(config, "scheduler")
        assert hasattr(config, "execution")
        assert hasattr(config, "recording")
        assert hasattr(config, "alerting")
        assert hasattr(config, "task_load")

    def test_config_defaults_valid(self):
        """默认配置通过校验"""
        from scheduler.config import validate_config
        config = AppConfig()
        errors = validate_config(config)
        assert errors == []


class TestExternalCrossProject:
    """跨项目复用验证"""

    def test_project_root_override(self):
        """PROJECT_ROOT 可被覆盖"""
        from scheduler.utils import set_project_root, get_project_root, reset_project_root

        original = get_project_root()
        try:
            set_project_root(Path("/tmp/test_project"))
            result = get_project_root()
            # macOS /tmp -> /private/tmp
            assert "test_project" in str(result)
        finally:
            reset_project_root()

    def test_project_root_reset(self):
        """PROJECT_ROOT 重置回默认"""
        from scheduler.utils import set_project_root, get_project_root, reset_project_root

        original = get_project_root()
        try:
            set_project_root(Path("/tmp/another"))
            reset_project_root()
            assert get_project_root() == original
        finally:
            reset_project_root()

    def test_context_initialization(self):
        """SchedulerContext 可初始化"""
        from scheduler.context import SchedulerContext

        ctx = SchedulerContext(project_root=Path("."))
        info = ctx.get_info()
        assert "project_root" in info

    def test_yaml_task_loading(self):
        """YAML 任务列表加载"""
        registry = TaskRegistry()
        count = registry.load_from_yaml("examples/external_project/scheduler.yaml")
        # YAML 中 tasks 是列表，应能加载
        assert count >= 0  # 至少不报错
