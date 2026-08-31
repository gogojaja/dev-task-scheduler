"""
任务注册表

管理所有任务的定义、注册、加载和查询。
支持装饰器注册、编程式注册、YAML 配置注册三种方式。
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Callable, Optional

from .models import TaskDefinition, TriggerType
from .state_store import get_state_store
from .utils import get_logger, get_project_root

logger = get_logger("scheduler.registry")


class TaskRegistry:
    """任务注册表

    管理所有任务定义的注册与查询。
    """

    def __init__(self):
        self._tasks: dict[str, TaskDefinition] = {}
        self._functions: dict[str, Callable] = {}  # 内存中的函数引用
        self._store = get_state_store()

    def register(self, task_def: TaskDefinition, func: Callable = None) -> TaskDefinition:
        """注册任务

        Args:
            task_def: 任务定义
            func: 函数对象（可选，装饰器注册时传入）

        Returns:
            注册后的任务定义
        """
        name = task_def.name

        if name in self._tasks:
            logger.warning(f"Task already registered, will overwrite: {name}")

        self._tasks[name] = task_def

        if func is not None:
            self._functions[name] = func

        # 持久化到数据库
        self._store.upsert_job(task_def)

        logger.info(f"Task registered: {name} ({task_def.trigger_type.value})")
        return task_def

    def register_function(
        self,
        func: Callable,
        name: str,
        trigger: str = "cron",
        description: str = "",
        idempotency_key: str = "{date}",
        max_retries: int = None,
        timeout: int = None,
        **trigger_kwargs,
    ) -> Callable:
        """注册函数为任务（装饰器内部实现）

        Args:
            func: 函数对象
            name: 任务名称
            trigger: 触发器类型 cron/interval/date
            description: 任务描述
            idempotency_key: 幂等键表达式
            max_retries: 最大重试次数
            timeout: 超时时间
            **trigger_kwargs: 触发器参数

        Returns:
            原函数（装饰器模式）
        """
        from .config import get_config
        config = get_config()

        # 构建触发器配置
        trigger_config = {}
        for key, value in trigger_kwargs.items():
            if value is not None:
                trigger_config[key] = value

        # 构建函数引用路径
        module = func.__module__
        func_name = func.__name__
        func_ref = f"{module}:{func_name}"

        task_def = TaskDefinition(
            name=name,
            func_ref=func_ref,
            trigger_type=TriggerType(trigger),
            trigger_config=trigger_config,
            description=description or func.__doc__ or "",
            max_retries=max_retries if max_retries is not None else config.execution.default_max_retries,
            timeout=timeout if timeout is not None else config.execution.default_timeout,
            idempotency_key_expr=idempotency_key,
            status="active",
        )

        self.register(task_def, func)
        return func

    def get(self, name: str) -> Optional[TaskDefinition]:
        """获取任务定义"""
        return self._tasks.get(name)

    def get_function(self, name: str) -> Optional[Callable]:
        """获取任务函数（内存中的引用）"""
        return self._functions.get(name)

    def list_all(self) -> list[TaskDefinition]:
        """列出所有已注册的任务"""
        return list(self._tasks.values())

    def list_active(self) -> list[TaskDefinition]:
        """列出所有活跃的任务"""
        return [t for t in self._tasks.values() if t.status == "active"]

    def load_from_module(self, module_path: str) -> int:
        """从 Python 模块加载任务

        模块中的函数如果使用了 @register_task 装饰器，
        导入时会自动注册。

        Args:
            module_path: 模块路径（如 "tools.scheduler.tasks.example"）

        Returns:
            加载的任务数
        """
        before_count = len(self._tasks)
        try:
            importlib.import_module(module_path)
        except ImportError as e:
            logger.error(f"Failed to load tasks from module '{module_path}': {e}")
            return 0

        after_count = len(self._tasks)
        loaded = after_count - before_count
        logger.info(f"Loaded {loaded} tasks from module: {module_path}")
        return loaded

    def load_from_yaml(self, yaml_path: str | Path) -> int:
        """从 YAML 配置文件加载任务

        Args:
            yaml_path: YAML 文件路径

        Returns:
            加载的任务数
        """
        import yaml

        path = Path(yaml_path)
        if not path.exists():
            logger.warning(f"Task config file not found: {path}")
            return 0

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            logger.error(f"Failed to parse task YAML '{path}': {e}")
            return 0

        tasks = data.get("tasks", [])
        count = 0

        for task_data in tasks:
            try:
                task_def = self._yaml_to_task_def(task_data)
                self.register(task_def)
                count += 1
            except Exception as e:
                logger.error(f"Failed to register task from YAML: {e}")

        logger.info(f"Loaded {count} tasks from YAML: {path}")
        return count

    def _yaml_to_task_def(self, data: dict) -> TaskDefinition:
        """将 YAML 数据转换为 TaskDefinition"""
        from .config import get_config
        config = get_config()

        trigger_data = data.get("trigger", {})
        trigger_type = trigger_data.get("type", "cron")

        # 构建触发器配置
        trigger_config = {}
        if trigger_type == "cron":
            cron_expr = trigger_data.get("cron", "")
            if cron_expr:
                # 解析 cron 表达式为 APScheduler 格式
                parts = cron_expr.split()
                if len(parts) >= 5:
                    trigger_config = {
                        "minute": parts[0],
                        "hour": parts[1],
                        "day": parts[2],
                        "month": parts[3],
                        "day_of_week": parts[4],
                    }
            # 也支持直接传参
            for key in ["year", "month", "day", "week", "day_of_week", "hour", "minute", "second"]:
                if key in trigger_data:
                    trigger_config[key] = trigger_data[key]

        elif trigger_type == "interval":
            for key in ["weeks", "days", "hours", "minutes", "seconds"]:
                if key in trigger_data:
                    trigger_config[key] = trigger_data[key]

        elif trigger_type == "date":
            if "run_date" in trigger_data:
                trigger_config["run_date"] = trigger_data["run_date"]

        return TaskDefinition(
            name=data["name"],
            func_ref=data.get("module", "") + ":" + data.get("function", ""),
            trigger_type=TriggerType(trigger_type),
            trigger_config=trigger_config,
            description=data.get("description", ""),
            max_retries=data.get("max_retries", config.execution.default_max_retries),
            timeout=data.get("timeout", config.execution.default_timeout),
            idempotency_key_expr=data.get("idempotency_key", "{date}"),
            params=data.get("params", {}),
        )

    def load_from_directory(self, dir_path: str | Path) -> int:
        """从目录加载所有任务模块

        扫描目录下的所有 .py 文件，导入它们以触发注册。

        Args:
            dir_path: 目录路径

        Returns:
            加载的任务数
        """
        dir_path = Path(dir_path)
        if not dir_path.exists():
            logger.warning(f"Task directory not found: {dir_path}")
            return 0

        total = 0
        # 将目录父路径加入 sys.path
        parent = str(dir_path.parent)
        if parent not in sys.path:
            sys.path.insert(0, parent)

        module_prefix = dir_path.name

        for py_file in sorted(dir_path.glob("*.py")):
            if py_file.name.startswith("_"):
                continue  # 跳过 __init__.py 等
            module_name = f"{module_prefix}.{py_file.stem}"
            total += self.load_from_module(module_name)

        return total


# 全局单例
task_registry = TaskRegistry()


def register_task(
    name: str,
    trigger: str = "cron",
    *,
    description: str = "",
    idempotency_key: str = "{date}",
    max_retries: int = None,
    timeout: int = None,
    # cron 参数
    year=None, month=None, day=None, week=None,
    day_of_week=None, hour=None, minute=None, second=None,
    # interval 参数
    weeks=0, days=0, hours=0, minutes=0, seconds=0,
    # date 参数
    run_date=None,
    # 其他
    **kwargs,
):
    """任务注册装饰器

    用法：
        @register_task(name="my_task", trigger="cron", hour=2, minute=0)
        def my_task():
            pass

    Args:
        name: 任务名称（唯一标识）
        trigger: 触发器类型 cron/interval/date
        description: 任务描述
        idempotency_key: 幂等键表达式
        max_retries: 最大重试次数
        timeout: 超时时间（秒）
        year, month, day, week, day_of_week, hour, minute, second: cron 触发器参数
        weeks, days, hours, minutes, seconds: interval 触发器参数
        run_date: date 触发器参数
    """
    # 收集触发器参数
    trigger_kwargs = {}

    if trigger == "cron":
        for key, val in [
            ("year", year), ("month", month), ("day", day),
            ("week", week), ("day_of_week", day_of_week),
            ("hour", hour), ("minute", minute), ("second", second),
        ]:
            if val is not None:
                trigger_kwargs[key] = val

    elif trigger == "interval":
        for key, val in [
            ("weeks", weeks), ("days", days), ("hours", hours),
            ("minutes", minutes), ("seconds", seconds),
        ]:
            if val:
                trigger_kwargs[key] = val

    elif trigger == "date":
        if run_date is not None:
            trigger_kwargs["run_date"] = run_date

    def decorator(func):
        task_registry.register_function(
            func=func,
            name=name,
            trigger=trigger,
            description=description,
            idempotency_key=idempotency_key,
            max_retries=max_retries,
            timeout=timeout,
            **trigger_kwargs,
        )
        return func

    return decorator
