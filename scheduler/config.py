"""
配置管理

支持 YAML 配置文件和编程式配置，提供统一的配置访问接口。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .utils import get_project_root, ensure_dir, get_logger

logger = get_logger("scheduler.config")


@dataclass
class SchedulerConfig:
    """调度器配置"""
    timezone: str = "Asia/Shanghai"
    jobstore_path: str = ".secrets/scheduler.db"
    executor_type: str = "threadpool"
    max_workers: int = 10
    misfire_grace_time: int = 3600  # 错过触发宽限时间（秒）
    coalesce: bool = True  # 多次 misfire 是否合并执行


@dataclass
class ExecutionConfig:
    """执行配置"""
    default_max_retries: int = 3
    default_timeout: int = 300
    retry_base_delay: int = 60  # 初始重试延迟（秒）
    retry_max_delay: int = 3600  # 最大重试延迟（秒）
    retry_jitter: int = 30  # 重试抖动范围（秒）
    retry_backoff_factor: float = 2.0  # 指数退避因子


@dataclass
class RecordingConfig:
    """记录配置"""
    enabled: bool = True
    csv_path: str = "台账/31_定时任务执行记录.csv"
    archive_days: int = 90  # SQLite 执行记录保留天数


@dataclass
class AlertingConfig:
    """告警配置"""
    enabled: bool = True
    system_notification: bool = True
    failed_alert_threshold: int = 1  # 失败几次后告警
    queue_alert_threshold: int = 50  # 队列堆积阈值
    heartbeat_timeout: int = 300  # 心跳超时（秒）
    webhook_url: str = ""  # 可选：webhook 告警地址


@dataclass
class TaskLoadConfig:
    """任务加载配置"""
    include_paths: list[str] = field(default_factory=list)
    config_files: list[str] = field(default_factory=list)


@dataclass
class AppConfig:
    """应用总配置"""
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    recording: RecordingConfig = field(default_factory=RecordingConfig)
    alerting: AlertingConfig = field(default_factory=AlertingConfig)
    task_load: TaskLoadConfig = field(default_factory=TaskLoadConfig)


# ─── 配置单例 ───────────────────────────────────────────────

_global_config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    """获取全局配置"""
    global _global_config
    if _global_config is None:
        _global_config = AppConfig()
    return _global_config


def set_config(config: AppConfig) -> None:
    """设置全局配置"""
    global _global_config
    _global_config = config


# ─── 配置加载 ───────────────────────────────────────────────

def load_config_from_yaml(config_path: str | Path) -> AppConfig:
    """从 YAML 文件加载配置

    Args:
        config_path: YAML 配置文件路径

    Returns:
        加载后的 AppConfig
    """
    import yaml

    path = Path(config_path)
    if not path.exists():
        logger.warning(f"Config file not found: {path}, using default config")
        return get_config()

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(f"Failed to load config from {path}: {e}")
        return get_config()

    config = AppConfig()

    # scheduler
    if "scheduler" in data:
        s = data["scheduler"]
        config.scheduler.timezone = s.get("timezone", config.scheduler.timezone)
        config.scheduler.jobstore_path = s.get("jobstore", {}).get("path", config.scheduler.jobstore_path) \
            if "jobstore" in s else config.scheduler.jobstore_path
        config.scheduler.executor_type = s.get("executor", {}).get("type", config.scheduler.executor_type) \
            if "executor" in s else config.scheduler.executor_type
        config.scheduler.max_workers = s.get("executor", {}).get("max_workers", config.scheduler.max_workers) \
            if "executor" in s else config.scheduler.max_workers
        config.scheduler.misfire_grace_time = s.get("misfire_grace_time", config.scheduler.misfire_grace_time)
        config.scheduler.coalesce = s.get("coalesce", config.scheduler.coalesce)

    # execution
    if "execution" in data:
        e = data["execution"]
        config.execution.default_max_retries = e.get("default_max_retries", config.execution.default_max_retries)
        config.execution.default_timeout = e.get("default_timeout", config.execution.default_timeout)
        config.execution.retry_base_delay = e.get("retry_base_delay", config.execution.retry_base_delay)
        config.execution.retry_max_delay = e.get("retry_max_delay", config.execution.retry_max_delay)
        config.execution.retry_jitter = e.get("retry_jitter", config.execution.retry_jitter)
        config.execution.retry_backoff_factor = e.get("retry_backoff_factor", config.execution.retry_backoff_factor)

    # recording
    if "recording" in data:
        r = data["recording"]
        config.recording.enabled = r.get("enabled", config.recording.enabled)
        config.recording.csv_path = r.get("csv_path", config.recording.csv_path)
        config.recording.archive_days = r.get("archive_days", config.recording.archive_days)

    # alerting
    if "alerting" in data:
        a = data["alerting"]
        config.alerting.enabled = a.get("enabled", config.alerting.enabled)
        config.alerting.system_notification = a.get("system_notification", config.alerting.system_notification)
        config.alerting.failed_alert_threshold = a.get("failed_alert_threshold", config.alerting.failed_alert_threshold)
        config.alerting.queue_alert_threshold = a.get("queue_alert_threshold", config.alerting.queue_alert_threshold)
        config.alerting.heartbeat_timeout = a.get("heartbeat_timeout", config.alerting.heartbeat_timeout)
        config.alerting.webhook_url = a.get("webhook_url", config.alerting.webhook_url)

    # task_load
    if "tasks" in data:
        t = data["tasks"]
        config.task_load.include_paths = t.get("include_paths", config.task_load.include_paths)
        config.task_load.config_files = t.get("config_files", config.task_load.config_files)

    set_config(config)
    logger.info(f"Config loaded from {path}")
    return config


def resolve_path(path_str: str) -> Path:
    """解析相对路径为绝对路径（相对于项目根）"""
    p = Path(path_str)
    if p.is_absolute():
        return p
    return get_project_root() / p


def ensure_storage_dirs() -> None:
    """确保存储目录存在"""
    config = get_config()

    # 确保 .secrets 目录存在
    db_path = resolve_path(config.scheduler.jobstore_path)
    ensure_dir(db_path.parent)

    # 确保 CSV 台账目录存在
    csv_path = resolve_path(config.recording.csv_path)
    ensure_dir(csv_path.parent)

    # 确保日志目录
    log_dir = get_project_root() / "logs" / "scheduler"
    ensure_dir(log_dir)
