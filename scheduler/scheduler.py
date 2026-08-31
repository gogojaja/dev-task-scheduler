"""
调度引擎管理器

基于 APScheduler 封装，集成任务注册、执行框架、幂等校验、
重试策略、告警通知等能力，提供统一的调度器管理接口。
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Optional

__version__ = "1.0.0"

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger

from .models import TaskDefinition, TriggerType, TaskStatus, TaskResult
from .registry import task_registry, TaskRegistry
from .executor import get_executor, TaskExecutor
from .state_store import get_state_store, StateStore
from .notifier import get_notifier, Notifier
from .retry import get_retry_policy, RetryPolicy
from .config import get_config, ensure_storage_dirs
from .utils import get_logger, now, get_node_id

logger = get_logger("scheduler.engine")


class SchedulerManager:
    """调度器管理器

    封装 APScheduler，集成所有子系统。
    单例模式，全局唯一调度器。
    """

    def __init__(
        self,
        registry: TaskRegistry = None,
        executor: TaskExecutor = None,
        store: StateStore = None,
        notifier: Notifier = None,
        retry_policy: RetryPolicy = None,
    ):
        self.registry = registry or task_registry
        self.executor = executor or get_executor()
        self.store = store or get_state_store()
        self.notifier = notifier or get_notifier()
        self.retry_policy = retry_policy or get_retry_policy()

        self._scheduler: Optional[BackgroundScheduler] = None
        self._running = False
        self._lock = threading.Lock()
        self._heartbeat_timer: Optional[threading.Timer] = None

    # ─── 生命周期 ──────────────────────────────────────────────

    def start(self) -> bool:
        """启动调度器

        Returns:
            是否启动成功
        """
        with self._lock:
            if self._running:
                logger.warning("Scheduler is already running")
                return True

            try:
                # 确保存储目录存在
                ensure_storage_dirs()

                # 创建调度器
                config = get_config()
                self._scheduler = BackgroundScheduler(
                    timezone=config.scheduler.timezone,
                    job_defaults={
                        "misfire_grace_time": config.scheduler.misfire_grace_time,
                        "coalesce": config.scheduler.coalesce,
                        "max_instances": 1,
                    },
                )

                # 配置执行器
                if config.scheduler.executor_type == "threadpool":
                    self._scheduler.add_executor(
                        "default",
                        "threadpool",
                        max_workers=config.scheduler.max_workers,
                    )

                # 从注册表加载所有活跃任务
                self._load_registered_tasks()

                # 启动调度器
                self._scheduler.start()
                self._running = True

                # 更新状态
                self.store.mark_started(__version__)

                # 启动心跳
                self._start_heartbeat()

                logger.info(f"Scheduler started successfully (version {__version__})")
                logger.info(f"Loaded {len(self._scheduler.get_jobs())} jobs")

                return True

            except Exception as e:
                logger.error(f"Failed to start scheduler: {e}")
                self._scheduler = None
                self._running = False
                return False

    def stop(self, wait: bool = True) -> None:
        """停止调度器

        Args:
            wait: 是否等待正在执行的任务完成
        """
        with self._lock:
            if not self._running or not self._scheduler:
                return

            try:
                # 停止心跳
                self._stop_heartbeat()

                # 关闭调度器
                self._scheduler.shutdown(wait=wait)
                self._running = False

                # 更新状态
                self.store.update_scheduler_state("stopped", heartbeat=False)

                logger.info("Scheduler stopped")

            except Exception as e:
                logger.error(f"Error stopping scheduler: {e}")

    def is_running(self) -> bool:
        """调度器是否在运行"""
        return self._running and self._scheduler is not None

    # ─── 任务管理 ──────────────────────────────────────────────

    def _load_registered_tasks(self):
        """加载所有已注册的活跃任务到 APScheduler"""
        for task_def in self.registry.list_active():
            self._add_job_to_scheduler(task_def)

    def _add_job_to_scheduler(self, task_def: TaskDefinition) -> bool:
        """将任务添加到 APScheduler

        Returns:
            是否添加成功
        """
        if not self._scheduler:
            return False

        try:
            trigger = self._build_trigger(task_def)
            if trigger is None:
                logger.error(f"Cannot build trigger for task: {task_def.name}")
                return False

            self._scheduler.add_job(
                self._execute_job_wrapper,
                trigger=trigger,
                id=task_def.name,
                name=task_def.name,
                args=[task_def.name],
                misfire_grace_time=get_config().scheduler.misfire_grace_time,
                coalesce=get_config().scheduler.coalesce,
                max_instances=1,
                replace_existing=True,
            )

            # 更新下次执行时间
            job = self._scheduler.get_job(task_def.name)
            if job and job.next_run_time:
                self.store.update_job_next_run(task_def.name, job.next_run_time)

            logger.debug(f"Job added to scheduler: {task_def.name}")
            return True

        except Exception as e:
            logger.error(f"Failed to add job '{task_def.name}': {e}")
            return False

    def _build_trigger(self, task_def: TaskDefinition):
        """构建 APScheduler 触发器"""
        cfg = task_def.trigger_config

        if task_def.trigger_type == TriggerType.CRON:
            return CronTrigger(
                year=cfg.get("year"),
                month=cfg.get("month"),
                day=cfg.get("day"),
                week=cfg.get("week"),
                day_of_week=cfg.get("day_of_week"),
                hour=cfg.get("hour"),
                minute=cfg.get("minute"),
                second=cfg.get("second", 0),
                timezone=get_config().scheduler.timezone,
            )

        elif task_def.trigger_type == TriggerType.INTERVAL:
            return IntervalTrigger(
                weeks=cfg.get("weeks", 0),
                days=cfg.get("days", 0),
                hours=cfg.get("hours", 0),
                minutes=cfg.get("minutes", 0),
                seconds=cfg.get("seconds", 0),
                timezone=get_config().scheduler.timezone,
            )

        elif task_def.trigger_type == TriggerType.DATE:
            run_date = cfg.get("run_date")
            if isinstance(run_date, str):
                from .utils import parse_datetime
                run_date = parse_datetime(run_date)
            return DateTrigger(
                run_date=run_date,
                timezone=get_config().scheduler.timezone,
            )

        return None

    def _execute_job_wrapper(self, task_name: str):
        """APScheduler 任务执行包装器

        负责调用执行框架，并处理重试逻辑。
        """
        task_def = self.registry.get(task_name)
        if not task_def:
            logger.error(f"Task not found in registry: {task_name}")
            return

        # 获取当前重试次数（通过 run_id 跟踪，这里简化处理）
        # 实际的重试通过 APScheduler 的 retry 或手动重新调度实现
        try:
            result = self.executor.execute_task(
                task_def=task_def,
                trigger_type="schedule",
            )

            # 失败处理
            if not result.success:
                # 失败告警
                self.notifier.alert_task_failed(
                    task_name=task_name,
                    error_message=result.message,
                    retry_count=0,  # 简化：具体重试由单独机制处理
                )

                # 判断是否进入死信
                # 注：完整的重试机制需要结合任务状态跟踪，
                # 当前 MVP 版本依赖 APScheduler misfire 和单次告警
                # 完整重试可通过自定义 JobStore + retry 计数实现

        except Exception as e:
            logger.error(f"Unexpected error in job wrapper: {task_name} - {e}")

    def add_task(self, task_def: TaskDefinition) -> bool:
        """动态添加任务

        Args:
            task_def: 任务定义

        Returns:
            是否添加成功
        """
        # 注册到注册表
        self.registry.register(task_def)

        # 如果调度器正在运行，添加到调度器
        if self._running and self._scheduler:
            return self._add_job_to_scheduler(task_def)

        return True

    def remove_task(self, task_name: str) -> bool:
        """移除任务"""
        if self._running and self._scheduler:
            try:
                self._scheduler.remove_job(task_name)
            except Exception as e:
                logger.debug(f"Remove job from scheduler failed: {e}")

        # 更新状态为 disabled
        self.store.update_job_status(task_name, "disabled")
        return True

    def pause_task(self, task_name: str) -> bool:
        """暂停任务"""
        if self._running and self._scheduler:
            try:
                self._scheduler.pause_job(task_name)
            except Exception as e:
                logger.debug(f"Pause job failed: {e}")
                return False

        self.store.update_job_status(task_name, "paused")
        logger.info(f"Task paused: {task_name}")
        return True

    def resume_task(self, task_name: str) -> bool:
        """恢复任务"""
        if self._running and self._scheduler:
            try:
                self._scheduler.resume_job(task_name)
            except Exception as e:
                logger.debug(f"Resume job failed: {e}")
                return False

        self.store.update_job_status(task_name, "active")
        logger.info(f"Task resumed: {task_name}")
        return True

    def run_task_now(self, task_name: str) -> Optional[TaskResult]:
        """立即手动执行任务

        Args:
            task_name: 任务名称

        Returns:
            执行结果
        """
        task_def = self.registry.get(task_name)
        if not task_def:
            logger.error(f"Task not found: {task_name}")
            return None

        logger.info(f"Manual trigger task: {task_name}")
        return self.executor.execute_task(
            task_def=task_def,
            trigger_type="manual",
        )

    # ─── 状态查询 ──────────────────────────────────────────────

    def get_job_info(self, task_name: str) -> Optional[dict]:
        """获取任务调度信息"""
        if not self._scheduler:
            return None

        try:
            job = self._scheduler.get_job(task_name)
            if not job:
                return None

            return {
                "id": job.id,
                "name": job.name,
                "trigger": str(job.trigger),
                "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
                "pending": bool(job.pending),
            }
        except Exception:
            return None

    def list_jobs(self) -> list[dict]:
        """列出所有调度中的任务"""
        if not self._scheduler:
            return []

        try:
            jobs = self._scheduler.get_jobs()
            return [
                {
                    "id": job.id,
                    "name": job.name,
                    "trigger": str(job.trigger),
                    "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
                }
                for job in jobs
            ]
        except Exception:
            return []

    def get_stats(self) -> dict:
        """获取调度器统计信息"""
        from .record_writer import get_record_writer

        jobs = self.list_jobs()
        today_stats = get_record_writer().get_today_stats()

        return {
            "running": self._running,
            "total_jobs": len(jobs),
            "active_jobs": len(self.registry.list_active()),
            "today_total": today_stats.get("total", 0),
            "today_success": today_stats.get("success", 0),
            "today_failed": today_stats.get("failed", 0),
            "today_dlq": today_stats.get("dlq", 0),
            "node_id": get_node_id(),
            "version": __version__,
        }

    # ─── 心跳机制 ──────────────────────────────────────────────

    def _start_heartbeat(self):
        """启动心跳定时器"""
        self._heartbeat_timer = threading.Timer(60, self._heartbeat_tick)
        self._heartbeat_timer.daemon = True
        self._heartbeat_timer.start()

    def _stop_heartbeat(self):
        """停止心跳定时器"""
        if self._heartbeat_timer:
            self._heartbeat_timer.cancel()
            self._heartbeat_timer = None

    def _heartbeat_tick(self):
        """心跳回调"""
        try:
            self.store.heartbeat()
            logger.debug("Heartbeat updated")
        except Exception as e:
            logger.error(f"Heartbeat failed: {e}")
        finally:
            # 重新调度下一次心跳
            if self._running:
                self._start_heartbeat()

    # ─── TRAE Schedule 集成 ───────────────────────────────────

    def sync_from_trae_schedule(self, tasks: list[dict]) -> int:
        """从 TRAE Schedule 同步任务

        Args:
            tasks: TRAE Schedule 任务列表

        Returns:
            同步的任务数
        """
        count = 0
        for task_data in tasks:
            try:
                task_def = TaskDefinition(
                    name=task_data["name"],
                    func_ref=task_data.get("func_ref", "tools.scheduler.tasks:placeholder"),
                    trigger_type=TriggerType.CRON,
                    trigger_config={"cron": task_data.get("cron", "0 0 * * *")},
                    description=task_data.get("description", ""),
                )
                self.add_task(task_def)
                count += 1
            except Exception as e:
                logger.error(f"Failed to sync task from TRAE: {e}")

        logger.info(f"Synced {count} tasks from TRAE Schedule")
        return count


# 全局单例
_scheduler_manager: Optional[SchedulerManager] = None


def get_scheduler() -> SchedulerManager:
    """获取全局调度器管理器单例"""
    global _scheduler_manager
    if _scheduler_manager is None:
        _scheduler_manager = SchedulerManager()
    return _scheduler_manager
