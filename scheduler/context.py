"""
项目上下文管理

支持多项目隔离运行，每个项目拥有独立的：
- 配置（AppConfig）
- 状态存储（SQLite）
- 记录写入器（CSV）
- 注册表 / 幂等管理器 / 重试策略

接入方式：
    from scheduler.context import SchedulerContext

    # 方式 1：使用默认项目根
    ctx = SchedulerContext()

    # 方式 2：指定外部项目根
    ctx = SchedulerContext(project_root="/path/to/my-project")

    # 方式 3：指定独立配置文件
    ctx = SchedulerContext(config_path="/path/to/scheduler.yaml")

    # 初始化并启动
    ctx.initialize()
    scheduler = ctx.create_scheduler()
    scheduler.start()
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .utils import get_logger, set_project_root, get_project_root
from .config import (
    AppConfig, set_config, get_config,
    load_config_from_yaml, apply_env_overrides, enforce_validate,
)

logger = get_logger("scheduler.context")


class SchedulerContext:
    """调度器项目上下文

    管理单个项目实例的全部状态，支持多项目隔离运行。
    """

    def __init__(
        self,
        project_root: str | Path = None,
        config_path: str | Path = None,
        env_prefix: str = "SCHEDULER_",
    ):
        """初始化上下文

        Args:
            project_root: 项目根目录，None 使用默认
            config_path: 配置文件路径，None 使用默认
            env_prefix: 环境变量前缀，默认 SCHEDULER_
        """
        self._project_root = Path(project_root).resolve() if project_root else None
        self._config_path = Path(config_path) if config_path else None
        self._env_prefix = env_prefix
        self._initialized = False

        # 延迟创建的组件
        self._config: Optional[AppConfig] = None
        self._store = None
        self._registry = None
        self._idempotency = None
        self._retry_policy = None
        self._record_writer = None
        self._notifier = None

    @property
    def project_root(self) -> Path:
        """项目根目录"""
        if self._project_root:
            return self._project_root
        return get_project_root()

    @property
    def config(self) -> AppConfig:
        """当前配置"""
        if self._config is None:
            self._config = get_config()
        return self._config

    @property
    def is_initialized(self) -> bool:
        """是否已初始化"""
        return self._initialized

    def initialize(self) -> "SchedulerContext":
        """初始化上下文

        1. 设置项目根
        2. 加载配置
        3. 创建组件

        Returns:
            self（支持链式调用）
        """
        # 1. 设置项目根
        if self._project_root:
            set_project_root(self._project_root)
            logger.info(f"Project root set to: {self._project_root}")

        # 2. 加载配置
        if self._config_path:
            self._config = load_config_from_yaml(self._config_path)
        else:
            self._config = get_config()
            apply_env_overrides(self._config)

        set_config(self._config)

        # 3. 确保存储目录存在
        from .config import ensure_storage_dirs
        ensure_storage_dirs()

        # 4. 创建组件
        self._create_components()

        self._initialized = True
        logger.info(f"SchedulerContext initialized for project: {self.project_root}")
        return self

    def _create_components(self):
        """创建各组件实例"""
        from .state_store import StateStore
        from .registry import TaskRegistry
        from .idempotency import IdempotencyManager
        from .retry import RetryPolicy
        from .record_writer import RecordWriter
        from .notifier import Notifier

        config = self._config

        # 状态存储
        self._store = StateStore()

        # 任务注册表
        self._registry = TaskRegistry()

        # 幂等管理器
        self._idempotency = IdempotencyManager()

        # 重试策略
        self._retry_policy = RetryPolicy.from_config()

        # 记录写入器
        self._record_writer = RecordWriter()

        # 通知器
        self._notifier = Notifier()

    def create_scheduler(self):
        """创建调度器实例

        Returns:
            Scheduler 实例
        """
        if not self._initialized:
            raise RuntimeError("Context not initialized. Call initialize() first.")

        from .scheduler import Scheduler
        return Scheduler(
            store=self._store,
            registry=self._registry,
        )

    def get_info(self) -> dict:
        """获取上下文信息

        Returns:
            上下文信息字典
        """
        return {
            "project_root": str(self.project_root),
            "config_path": str(self._config_path) if self._config_path else None,
            "initialized": self._initialized,
            "timezone": self._config.scheduler.timezone if self._config else None,
            "jobstore_path": self._config.scheduler.jobstore_path if self._config else None,
            "max_workers": self._config.scheduler.max_workers if self._config else None,
            "recording_enabled": self._config.recording.enabled if self._config else None,
            "alerting_enabled": self._config.alerting.enabled if self._config else None,
        }

    def shutdown(self):
        """关闭上下文，释放资源"""
        if self._store:
            self._store.close()
            logger.info("StateStore closed")

        self._initialized = False
        logger.info(f"SchedulerContext shutdown for project: {self.project_root}")


def get_context_info() -> dict:
    """获取当前上下文摘要信息

    Returns:
        信息字典
    """
    config = get_config()
    return {
        "project_root": str(get_project_root()),
        "timezone": config.scheduler.timezone,
        "max_workers": config.scheduler.max_workers,
        "recording_enabled": config.recording.enabled,
        "alerting_enabled": config.alerting.enabled,
    }
