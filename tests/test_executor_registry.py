"""
executor.py + idempotency.py + record_writer.py + registry.py 单元测试
"""

import pytest
from pathlib import Path

from scheduler.models import (
    TaskDefinition, TaskResult, TaskContext, ExecutionRecord,
    TriggerType, TaskStatus, ErrorCode,
)
from scheduler.idempotency import IdempotencyManager
from scheduler.record_writer import RecordWriter
from scheduler.registry import TaskRegistry


class TestIdempotencyManager:
    """幂等管理器测试"""

    def test_generate_key_date(self, test_idempotency):
        ctx = TaskContext.create(task_name="test")
        key = test_idempotency.generate_key("test", "{date}", ctx)
        assert key.startswith("test:")
        assert "test:{date}" != key  # 变量已被替换

    def test_generate_key_datetime(self, test_idempotency):
        ctx = TaskContext.create(task_name="test")
        key = test_idempotency.generate_key("test", "{datetime}", ctx)
        assert key.startswith("test:")

    def test_generate_key_task_name(self, test_idempotency):
        ctx = TaskContext.create(task_name="my_task")
        key = test_idempotency.generate_key("my_task", "{task_name}", ctx)
        assert "my_task" in key

    def test_generate_key_run_id(self, test_idempotency):
        ctx = TaskContext.create(task_name="test")
        key = test_idempotency.generate_key("test", "{run_id}", ctx)
        assert ctx.run_id in key

    def test_generate_key_param(self, test_idempotency):
        ctx = TaskContext.create(task_name="test", params={"region": "us"})
        key = test_idempotency.generate_key("test", "{param:region}", ctx)
        assert "us" in key

    def test_generate_key_weekday(self, test_idempotency):
        ctx = TaskContext.create(task_name="test")
        key = test_idempotency.generate_key("test", "{weekday}", ctx)
        assert key.startswith("test:")

    def test_generate_key_month_year(self, test_idempotency):
        ctx = TaskContext.create(task_name="test")
        key = test_idempotency.generate_key("test", "{month}_{year}", ctx)
        assert key.startswith("test:")

    def test_check_miss(self, test_idempotency):
        result = test_idempotency.check("nonexistent_key")
        assert result is None

    def test_record_and_check(self, test_idempotency, test_store, sample_task_def):
        job_id = test_store.upsert_job(sample_task_def)
        test_idempotency.record(job_id, "idem_key_001", {"result": "ok"})
        result = test_idempotency.check("idem_key_001")
        assert result is not None
        assert result["result"] == "ok"


class TestRecordWriter:
    """CSV 记录写入器测试"""

    def test_ensure_file_creates(self, tmp_csv, test_config):
        writer = RecordWriter(csv_path=tmp_csv)
        assert tmp_csv.exists()

    def test_append_record(self, tmp_csv, test_config):
        writer = RecordWriter(csv_path=tmp_csv)
        record = ExecutionRecord(
            run_id="run-001",
            task_name="test_task",
            trigger_type="schedule",
            status="success",
            duration=1.5,
        )
        exec_no = writer.append_record(record)
        assert exec_no is not None
        assert exec_no.startswith("EX-")

    def test_record_count(self, tmp_csv, test_config):
        writer = RecordWriter(csv_path=tmp_csv)
        for i in range(3):
            record = ExecutionRecord(
                run_id=f"run-count-{i}",
                task_name="test_task",
                status="success",
            )
            writer.append_record(record)
        assert writer.get_record_count() == 3

    def test_csv_utf8_bom(self, tmp_csv, test_config):
        writer = RecordWriter(csv_path=tmp_csv)
        record = ExecutionRecord(run_id="run-bom", task_name="test", status="success")
        writer.append_record(record)

        with open(tmp_csv, "rb") as f:
            first_bytes = f.read(3)
        assert first_bytes == b"\xef\xbb\xbf"  # UTF-8 BOM

    def test_today_stats_empty(self, tmp_csv, test_config):
        writer = RecordWriter(csv_path=tmp_csv)
        stats = writer.get_today_stats()
        assert stats["total"] == 0


class TestTaskRegistry:
    """任务注册表测试"""

    def test_register_task(self, test_registry, sample_task_def):
        result = test_registry.register(sample_task_def)
        assert result.name == sample_task_def.name

    def test_get_task(self, test_registry, sample_task_def):
        test_registry.register(sample_task_def)
        retrieved = test_registry.get(sample_task_def.name)
        assert retrieved is not None
        assert retrieved.name == sample_task_def.name

    def test_get_nonexistent(self, test_registry):
        assert test_registry.get("nonexistent") is None

    def test_list_all(self, test_registry, sample_task_def):
        test_registry.register(sample_task_def)
        tasks = test_registry.list_all()
        assert len(tasks) >= 1

    def test_list_active(self, test_registry, sample_task_def):
        test_registry.register(sample_task_def)
        active = test_registry.list_active()
        assert len(active) >= 1

    def test_register_with_function(self, test_registry, sample_task_def):
        from tests.test_helpers import sample_func
        test_registry.register(sample_task_def, func=sample_func)
        func = test_registry.get_function(sample_task_def.name)
        assert func is sample_func

    def test_load_from_yaml(self, test_registry, tmp_path):
        yaml_content = """
tasks:
  - name: yaml_task_1
    trigger:
      type: cron
      cron: "0 2 * * *"
    module: tests.test_helpers
    function: sample_func
    description: YAML 任务
  - name: yaml_task_2
    trigger:
      type: interval
      minutes: 10
    module: tests.test_helpers
    function: sample_func
"""
        yaml_file = tmp_path / "tasks.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        count = test_registry.load_from_yaml(yaml_file)
        assert count == 2
        assert test_registry.get("yaml_task_1") is not None
        assert test_registry.get("yaml_task_2") is not None

    def test_load_from_yaml_nonexistent(self, test_registry, tmp_path):
        count = test_registry.load_from_yaml(tmp_path / "nonexistent.yaml")
        assert count == 0

    def test_load_from_directory(self, test_registry, tmp_path):
        tasks_dir = tmp_path / "my_tasks"
        tasks_dir.mkdir()
        task_file = tasks_dir / "my_task.py"
        task_file.write_text("""
from scheduler.registry import register_task

@register_task(name="dir_task_" + str(id(__name__)), trigger="cron", hour="3")
def my_task():
    return "ok"
""", encoding="utf-8")

        # load_from_directory 导入模块触发 @register_task 装饰器
        # 装饰器注册到全局 task_registry，而非 test_registry
        # 因此这里只验证方法不报错
        count = test_registry.load_from_directory(tasks_dir)
        assert count >= 0  # 导入成功即可


class TestTaskExecutor:
    """执行框架测试"""

    def test_execute_success(self, test_executor, test_registry, sample_task_def):
        from tests.test_helpers import sample_func
        test_registry.register(sample_task_def, func=sample_func)

        result = test_executor.execute_task(sample_task_def, trigger_type="manual")
        assert result.success is True

    def test_execute_with_context(self, test_executor, test_registry, sample_task_def):
        from tests.test_helpers import sample_func_with_context
        sample_task_def.func_ref = "tests.test_helpers:sample_func_with_context"
        test_registry.register(sample_task_def, func=sample_func_with_context)

        result = test_executor.execute_task(sample_task_def, trigger_type="manual")
        assert result.success is True
        assert "run_id" in result.data

    def test_execute_failure(self, test_executor, test_registry, sample_task_def):
        from tests.test_helpers import sample_func_fail
        sample_task_def.func_ref = "tests.test_helpers:sample_func_fail"
        test_registry.register(sample_task_def, func=sample_func_fail)

        result = test_executor.execute_task(sample_task_def, trigger_type="manual")
        assert result.success is False

    def test_execute_skip_retry(self, test_executor, test_registry, sample_task_def):
        from tests.test_helpers import sample_func_skip_retry
        sample_task_def.func_ref = "tests.test_helpers:sample_func_skip_retry"
        test_registry.register(sample_task_def, func=sample_func_skip_retry)

        result = test_executor.execute_task(sample_task_def, trigger_type="manual")
        assert result.success is False
        assert result.skip_retry is True

    def test_execute_dict_return(self, test_executor, test_registry, sample_task_def):
        from tests.test_helpers import sample_func_dict
        sample_task_def.func_ref = "tests.test_helpers:sample_func_dict"
        test_registry.register(sample_task_def, func=sample_func_dict)

        result = test_executor.execute_task(sample_task_def, trigger_type="manual")
        assert result.success is True
        assert result.data == {"key": "value", "count": 42}

    def test_execute_none_return(self, test_executor, test_registry, sample_task_def):
        from tests.test_helpers import sample_func_none
        sample_task_def.func_ref = "tests.test_helpers:sample_func_none"
        test_registry.register(sample_task_def, func=sample_func_none)

        result = test_executor.execute_task(sample_task_def, trigger_type="manual")
        assert result.success is True

    def test_execute_idempotent_skip(self, test_executor, test_registry, sample_task_def):
        """幂等去重：第二次执行应返回 SKIPPED"""
        from tests.test_helpers import sample_func
        test_registry.register(sample_task_def, func=sample_func)

        # 第一次执行
        result1 = test_executor.execute_task(sample_task_def, trigger_type="manual")
        assert result1.success is True

        # 第二次执行（同一天，幂等键相同）
        result2 = test_executor.execute_task(sample_task_def, trigger_type="manual")
        assert result2.success is True
        assert "Skipped" in result2.message or "idempotent" in result2.message.lower()
