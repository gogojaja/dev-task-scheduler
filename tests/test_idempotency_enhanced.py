"""
idempotency.py 增强功能测试：键清理 / 边界处理 / 安全校验
"""

import pytest
from scheduler.idempotency import IdempotencyManager, MAX_KEY_LENGTH
from scheduler.models import TaskContext
from scheduler.utils import now


def _make_ctx(task_name="test", run_id="run-1", params=None):
    """创建测试用 TaskContext"""
    return TaskContext.create(task_name=task_name, params=params or {})


class TestGenerateKeyBoundary:
    """幂等键生成边界处理测试"""

    def test_empty_task_name_raises(self, test_idempotency):
        """空 task_name 抛出 ValueError"""
        ctx = _make_ctx(task_name="")
        with pytest.raises(ValueError, match="task_name cannot be empty"):
            test_idempotency.generate_key("", "{date}", ctx)

    def test_empty_expr_uses_default(self, test_idempotency):
        """空表达式使用默认 {date}"""
        ctx = _make_ctx()
        key = test_idempotency.generate_key("test", "", ctx)
        assert key.startswith("test:")
        from scheduler.utils import today_str
        assert today_str() in key

    def test_key_truncated_to_max_length(self, test_idempotency):
        """超长键被安全截断"""
        ctx = _make_ctx()
        long_expr = "a" * 600
        key = test_idempotency.generate_key("test", long_expr, ctx)
        assert len(key) <= MAX_KEY_LENGTH

    def test_key_with_special_characters(self, test_idempotency):
        """特殊字符处理"""
        ctx = _make_ctx(params={"path": "/a/b/c"})
        key = test_idempotency.generate_key("test", "{param:path}", ctx)
        assert "test:" in key
        assert "/a/b/c" in key

    def test_key_with_unicode(self, test_idempotency):
        """Unicode 字符处理"""
        ctx = _make_ctx(task_name="测试任务")
        key = test_idempotency.generate_key("测试任务", "{date}", ctx)
        assert key.startswith("测试任务:")

    def test_key_with_missing_param(self, test_idempotency):
        """缺失参数替换为空字符串"""
        ctx = _make_ctx(params={})
        key = test_idempotency.generate_key("test", "{param:missing}", ctx)
        assert key == "test:"

    def test_key_with_multiple_vars(self, test_idempotency):
        """多变量组合"""
        ctx = _make_ctx()
        key = test_idempotency.generate_key("test", "{date}_{weekday}_{month}", ctx)
        assert key.startswith("test:")
        parts = key.split(":")[1]
        assert "_" in parts


class TestCheckBoundary:
    """幂等校验边界处理测试"""

    def test_check_empty_key_raises(self, test_idempotency):
        """空键校验抛出 ValueError"""
        with pytest.raises(ValueError, match="cannot be empty"):
            test_idempotency.check("")

    def test_check_whitespace_key_raises(self, test_idempotency):
        """空白键校验抛出 ValueError"""
        with pytest.raises(ValueError, match="cannot be empty"):
            test_idempotency.check("   ")

    def test_check_nonexistent_key_returns_none(self, test_idempotency):
        """不存在的键返回 None"""
        result = test_idempotency.check("nonexistent-key-12345")
        assert result is None


class TestCheckOrSkip:
    """安全幂等校验测试（不抛异常）"""

    def test_empty_key_returns_false(self, test_idempotency):
        """空键不校验，返回 False（应执行）"""
        should_skip, result = test_idempotency.check_or_skip("")
        assert should_skip is False
        assert result is None

    def test_whitespace_key_returns_false(self, test_idempotency):
        """空白键不校验，返回 False"""
        should_skip, result = test_idempotency.check_or_skip("   ")
        assert should_skip is False

    def test_nonexistent_key_returns_false(self, test_idempotency):
        """不存在的键返回 False"""
        should_skip, result = test_idempotency.check_or_skip("new-key-12345")
        assert should_skip is False

    def test_existing_key_returns_true(self, test_idempotency, sample_task_def, test_store):
        """已存在的键返回 True"""
        job_id = test_store.upsert_job(sample_task_def)
        test_idempotency.record(job_id, "existing-key", {"result": "ok"})

        should_skip, result = test_idempotency.check_or_skip("existing-key")
        assert should_skip is True
        assert result == {"result": "ok"}


class TestRecordBoundary:
    """幂等记录边界处理测试"""

    def test_record_empty_key_raises(self, test_idempotency):
        """空键记录抛出 ValueError"""
        with pytest.raises(ValueError, match="cannot be empty"):
            test_idempotency.record(1, "", {"result": "ok"})

    def test_record_whitespace_key_raises(self, test_idempotency):
        """空白键记录抛出 ValueError"""
        with pytest.raises(ValueError, match="cannot be empty"):
            test_idempotency.record(1, "  ", {"result": "ok"})

    def test_record_and_check_roundtrip(self, test_idempotency, test_store, sample_task_def):
        """记录后校验完整流程"""
        job_id = test_store.upsert_job(sample_task_def)
        key = "roundtrip-key"

        # 记录前校验应返回 None
        assert test_idempotency.check(key) is None

        # 记录
        success = test_idempotency.record(job_id, key, {"data": 42})
        assert success is True

        # 记录后校验应返回结果
        result = test_idempotency.check(key)
        assert result == {"data": 42}

    def test_record_duplicate_returns_false(self, test_idempotency, test_store, sample_task_def):
        """重复记录返回 False"""
        job_id = test_store.upsert_job(sample_task_def)
        key = "duplicate-key"

        assert test_idempotency.record(job_id, key, {"first": True}) is True
        assert test_idempotency.record(job_id, key, {"second": True}) is False


class TestCleanupExpired:
    """过期幂等键清理测试"""

    def test_cleanup_delegates_to_store(self, test_idempotency, test_store, sample_task_def):
        """清理委托给 store"""
        job_id = test_store.upsert_job(sample_task_def)
        test_idempotency.record(job_id, "cleanup-test-key", {"result": "ok"})

        # 清理 90 天内的不会删除
        count = test_idempotency.cleanup_expired(days=90)
        assert count == 0

        # 键仍存在
        result = test_idempotency.check("cleanup-test-key")
        assert result is not None
