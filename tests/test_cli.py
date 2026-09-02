"""
cli.py 命令行接口测试
"""

import pytest
import sys
from unittest.mock import patch, MagicMock
from io import StringIO

from scheduler.cli import main, cmd_validate, cmd_cleanup, cmd_recover


class TestCLIParsing:
    """CLI 参数解析测试"""

    def test_no_command_shows_help(self, capsys):
        """无命令时显示帮助"""
        with patch("sys.argv", ["scheduler"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_validate_command_exists(self):
        """validate 命令可解析"""
        with patch("sys.argv", ["scheduler", "validate"]):
            with patch("scheduler.cli.cmd_validate") as mock:
                main()
                mock.assert_called_once()

    def test_validate_with_config(self):
        """validate --config 参数可解析"""
        with patch("sys.argv", ["scheduler", "validate", "--config", "test.yaml"]):
            with patch("scheduler.cli.cmd_validate") as mock:
                main()
                args = mock.call_args[0][0]
                assert args.config == "test.yaml"

    def test_cleanup_command_exists(self):
        """cleanup 命令可解析"""
        with patch("sys.argv", ["scheduler", "cleanup"]):
            with patch("scheduler.cli.cmd_cleanup") as mock:
                main()
                mock.assert_called_once()

    def test_cleanup_with_days(self):
        """cleanup --days 参数可解析"""
        with patch("sys.argv", ["scheduler", "cleanup", "--days", "30"]):
            with patch("scheduler.cli.cmd_cleanup") as mock:
                main()
                args = mock.call_args[0][0]
                assert args.days == 30

    def test_recover_command_exists(self):
        """recover 命令可解析"""
        with patch("sys.argv", ["scheduler", "recover"]):
            with patch("scheduler.cli.cmd_recover") as mock:
                main()
                mock.assert_called_once()

    def test_history_with_filters(self):
        """history 命令带过滤器"""
        with patch("sys.argv", ["scheduler", "history", "--task-name", "test", "--status", "success", "--limit", "5"]):
            with patch("scheduler.cli.cmd_history") as mock:
                main()
                args = mock.call_args[0][0]
                assert args.task_name == "test"
                assert args.status == "success"
                assert args.limit == 5

    def test_dlq_with_limit(self):
        """dlq 命令带 limit"""
        with patch("sys.argv", ["scheduler", "dlq", "--limit", "10"]):
            with patch("scheduler.cli.cmd_dlq") as mock:
                main()
                args = mock.call_args[0][0]
                assert args.limit == 10


class TestCLIValidate:
    """validate 命令功能测试"""

    def test_validate_default_config(self, capsys):
        """默认配置校验通过"""
        args = MagicMock()
        args.config = None
        cmd_validate(args)
        captured = capsys.readouterr()
        assert "passed" in captured.out.lower() or "✅" in captured.out

    def test_validate_invalid_config(self, capsys):
        """非法配置校验失败"""
        args = MagicMock()
        args.config = None

        from scheduler.config import AppConfig
        bad_config = AppConfig()
        bad_config.scheduler.max_workers = -1

        with patch("scheduler.config.get_config", return_value=bad_config):
            with pytest.raises(SystemExit) as exc_info:
                cmd_validate(args)
            assert exc_info.value.code == 1


class TestCLICleanup:
    """cleanup 命令功能测试"""

    def test_cleanup_calls_store(self, test_store):
        """cleanup 调用 store 清理方法"""
        args = MagicMock()
        args.days = 90

        with patch("scheduler.state_store.get_state_store", return_value=test_store):
            cmd_cleanup(args)
        # 不抛异常即为成功


class TestCLIRecover:
    """recover 命令功能测试"""

    def test_recover_calls_store(self, test_store, capsys):
        """recover 调用 store 恢复方法"""
        with patch("scheduler.state_store.get_state_store", return_value=test_store):
            args = MagicMock()
            cmd_recover(args)
        captured = capsys.readouterr()
        assert "Recovery" in captured.out or "🔄" in captured.out
