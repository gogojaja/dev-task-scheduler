"""  
配置管理

支持 YAML 配置文件、环境变量覆盖和编程式配置，提供统一的配置访问接口。

配置优先级（从高到低）：
1. 环境变量（SCHEDULER_xxx）
2. YAML 配置文件
3. 默认值
"""  

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .utils import get_project_root, ensure_dir, get_logger

logger = get_logger("scheduler.config")


# ─── 配置校验异常 ────────────────────────────────────────────

class ConfigValidationError(Exception):
    """配置校验异常"""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"Config validation failed with {len(errors)} error(s):\n" + "\n".join(f"  - {e}" for e in errors))


# ─── 环境变量前缀 ────────────────────────────────────────────

ENV_PREFIX = "SCHEDULER_"


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
    task_defs: list[dict] = field(default_factory=list)  # YAML 内联任务定义（list 格式）


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

    # task_load：支持 dict 和 list 两种 tasks 格式
    if "tasks" in data:
        t = data["tasks"]
        if isinstance(t, dict):
            # dict 格式：{include_paths: [...], config_files: [...]}
            config.task_load.include_paths = t.get("include_paths", config.task_load.include_paths)
            config.task_load.config_files = t.get("config_files", config.task_load.config_files)
        elif isinstance(t, list):
            # list 格式：[{name: ..., func_ref: ..., ...}, ...]
            config.task_load.task_defs = t
        else:
            logger.warning(f"Invalid 'tasks' format in config: expected dict or list, got {type(t).__name__}")

    set_config(config)
    logger.info(f"Config loaded from {path}")

    # 加载后自动应用环境变量覆盖 + 校验
    apply_env_overrides(config)
    errors = validate_config(config)
    if errors:
        logger.warning(f"Config validation warnings: {len(errors)} issue(s)")
        for err in errors:
            logger.warning(f"  - {err}")

    return config


def resolve_path(path_str: str) -> Path:
    """解析相对路径为绝对路径（相对于项目根）"""
    p = Path(os.path.expanduser(path_str))
    if p.is_absolute():
        return p
    return get_project_root() / p


# ─── 环境变量覆盖 ────────────────────────────────────────────

def _env_int(name: str, default: int) -> int:
    """从环境变量读取整数"""
    val = os.environ.get(name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        logger.warning(f"Invalid int for env {name}={val!r}, using default {default}")
        return default


def _env_float(name: str, default: float) -> float:
    """从环境变量读取浮点数"""
    val = os.environ.get(name)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        logger.warning(f"Invalid float for env {name}={val!r}, using default {default}")
        return default


def _env_bool(name: str, default: bool) -> bool:
    """从环境变量读取布尔值（支持 true/false/1/0/yes/no）"""
    val = os.environ.get(name)
    if val is None:
        return default
    lower = val.strip().lower()
    if lower in ("true", "1", "yes", "on"):
        return True
    elif lower in ("false", "0", "no", "off"):
        return False
    else:
        logger.warning(f"Invalid bool for env {name}={val!r}, using default {default}")
        return default


def _env_str(name: str, default: str) -> str:
    """从环境变量读取字符串"""
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip()


def apply_env_overrides(config: AppConfig | None = None) -> AppConfig:
    """应用环境变量覆盖

    环境变量命名规则：SCHEDULER_{SECTION}_{FIELD}
    示例：SCHEDULER_TIMEZONE=UTC, SCHEDULER_MAX_WORKERS=5

    Args:
        config: 基础配置，为 None 时使用全局配置

    Returns:
        覆盖后的 AppConfig
    """
    if config is None:
        config = get_config()

    # scheduler 域
    config.scheduler.timezone = _env_str(f"{ENV_PREFIX}TIMEZONE", config.scheduler.timezone)
    config.scheduler.jobstore_path = _env_str(f"{ENV_PREFIX}JOBSTORE_PATH", config.scheduler.jobstore_path)
    config.scheduler.executor_type = _env_str(f"{ENV_PREFIX}EXECUTOR_TYPE", config.scheduler.executor_type)
    config.scheduler.max_workers = _env_int(f"{ENV_PREFIX}MAX_WORKERS", config.scheduler.max_workers)
    config.scheduler.misfire_grace_time = _env_int(f"{ENV_PREFIX}MISFIRE_GRACE_TIME", config.scheduler.misfire_grace_time)
    config.scheduler.coalesce = _env_bool(f"{ENV_PREFIX}COALESCE", config.scheduler.coalesce)

    # execution 域
    config.execution.default_max_retries = _env_int(f"{ENV_PREFIX}MAX_RETRIES", config.execution.default_max_retries)
    config.execution.default_timeout = _env_int(f"{ENV_PREFIX}TIMEOUT", config.execution.default_timeout)
    config.execution.retry_base_delay = _env_int(f"{ENV_PREFIX}RETRY_BASE_DELAY", config.execution.retry_base_delay)
    config.execution.retry_max_delay = _env_int(f"{ENV_PREFIX}RETRY_MAX_DELAY", config.execution.retry_max_delay)
    config.execution.retry_jitter = _env_int(f"{ENV_PREFIX}RETRY_JITTER", config.execution.retry_jitter)
    config.execution.retry_backoff_factor = _env_float(f"{ENV_PREFIX}RETRY_BACKOFF_FACTOR", config.execution.retry_backoff_factor)

    # recording 域
    config.recording.enabled = _env_bool(f"{ENV_PREFIX}RECORDING_ENABLED", config.recording.enabled)
    config.recording.csv_path = _env_str(f"{ENV_PREFIX}CSV_PATH", config.recording.csv_path)
    config.recording.archive_days = _env_int(f"{ENV_PREFIX}ARCHIVE_DAYS", config.recording.archive_days)

    # alerting 域
    config.alerting.enabled = _env_bool(f"{ENV_PREFIX}ALERTING_ENABLED", config.alerting.enabled)
    config.alerting.system_notification = _env_bool(f"{ENV_PREFIX}SYSTEM_NOTIFICATION", config.alerting.system_notification)
    config.alerting.failed_alert_threshold = _env_int(f"{ENV_PREFIX}FAILED_ALERT_THRESHOLD", config.alerting.failed_alert_threshold)
    config.alerting.queue_alert_threshold = _env_int(f"{ENV_PREFIX}QUEUE_ALERT_THRESHOLD", config.alerting.queue_alert_threshold)
    config.alerting.heartbeat_timeout = _env_int(f"{ENV_PREFIX}HEARTBEAT_TIMEOUT", config.alerting.heartbeat_timeout)
    config.alerting.webhook_url = _env_str(f"{ENV_PREFIX}WEBHOOK_URL", config.alerting.webhook_url)

    # task_load 域
    include_paths = os.environ.get(f"{ENV_PREFIX}INCLUDE_PATHS")
    if include_paths:
        config.task_load.include_paths = [p.strip() for p in include_paths.split(",") if p.strip()]

    config_files = os.environ.get(f"{ENV_PREFIX}CONFIG_FILES")
    if config_files:
        config.task_load.config_files = [p.strip() for p in config_files.split(",") if p.strip()]

    logger.debug("Environment variable overrides applied")
    return config


# ─── 配置校验 ───────────────────────────────────────────────

_VALID_TIMEZONES = {"Asia/Shanghai", "UTC", "US/Eastern", "US/Pacific", "Europe/London", "Asia/Tokyo"}


def validate_config(config: AppConfig | None = None) -> list[str]:
    """校验配置合法性

    Args:
        config: 待校验配置，为 None 时使用全局配置

    Returns:
        错误消息列表，空列表表示校验通过

    Raises:
        ConfigValidationError: 当调用 enforce_validate 且校验不通过时
    """
    if config is None:
        config = get_config()

    errors: list[str] = []

    # ── scheduler 域 ──
    sc = config.scheduler
    if not sc.timezone:
        errors.append("scheduler.timezone: 不能为空")
    if sc.max_workers < 1:
        errors.append(f"scheduler.max_workers: 必须 >= 1，当前值 {sc.max_workers}")
    if sc.max_workers > 100:
        errors.append(f"scheduler.max_workers: 必须 <= 100，当前值 {sc.max_workers}")
    if sc.misfire_grace_time < 0:
        errors.append(f"scheduler.misfire_grace_time: 必须 >= 0，当前值 {sc.misfire_grace_time}")
    if not sc.jobstore_path:
        errors.append("scheduler.jobstore_path: 不能为空")
    if sc.executor_type not in ("threadpool", "processpool"):
        errors.append(f"scheduler.executor_type: 必须为 threadpool 或 processpool，当前值 {sc.executor_type!r}")

    # ── execution 域 ──
    ec = config.execution
    if ec.default_max_retries < 0:
        errors.append(f"execution.default_max_retries: 必须 >= 0，当前值 {ec.default_max_retries}")
    if ec.default_max_retries > 100:
        errors.append(f"execution.default_max_retries: 必须 <= 100，当前值 {ec.default_max_retries}")
    if ec.default_timeout < 1:
        errors.append(f"execution.default_timeout: 必须 >= 1，当前值 {ec.default_timeout}")
    if ec.retry_base_delay < 0:
        errors.append(f"execution.retry_base_delay: 必须 >= 0，当前值 {ec.retry_base_delay}")
    if ec.retry_max_delay < 0:
        errors.append(f"execution.retry_max_delay: 必须 >= 0，当前值 {ec.retry_max_delay}")
    if ec.retry_max_delay < ec.retry_base_delay:
        errors.append(f"execution.retry_max_delay({ec.retry_max_delay}): 必须 >= retry_base_delay({ec.retry_base_delay})")
    if ec.retry_jitter < 0:
        errors.append(f"execution.retry_jitter: 必须 >= 0，当前值 {ec.retry_jitter}")
    if ec.retry_backoff_factor < 1.0:
        errors.append(f"execution.retry_backoff_factor: 必须 >= 1.0，当前值 {ec.retry_backoff_factor}")

    # ── recording 域 ──
    rc = config.recording
    if not rc.csv_path:
        errors.append("recording.csv_path: 不能为空")
    if rc.archive_days < 1:
        errors.append(f"recording.archive_days: 必须 >= 1，当前值 {rc.archive_days}")

    # ── alerting 域 ──
    ac = config.alerting
    if ac.failed_alert_threshold < 1:
        errors.append(f"alerting.failed_alert_threshold: 必须 >= 1，当前值 {ac.failed_alert_threshold}")
    if ac.queue_alert_threshold < 1:
        errors.append(f"alerting.queue_alert_threshold: 必须 >= 1，当前值 {ac.queue_alert_threshold}")
    if ac.heartbeat_timeout < 10:
        errors.append(f"alerting.heartbeat_timeout: 必须 >= 10，当前值 {ac.heartbeat_timeout}")
    if ac.webhook_url and not ac.webhook_url.startswith(("http://", "https://")):
        errors.append(f"alerting.webhook_url: 必须以 http:// 或 https:// 开头，当前值 {ac.webhook_url!r}")

    return errors


def enforce_validate(config: AppConfig | None = None) -> None:
    """强制校验配置，不通过则抛出异常

    Args:
        config: 待校验配置，为 None 时使用全局配置

    Raises:
        ConfigValidationError: 校验不通过时
    """
    errors = validate_config(config)
    if errors:
        raise ConfigValidationError(errors)


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
