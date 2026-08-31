"""
SQLite 状态存储

负责任务元数据、执行状态、幂等键等热数据的持久化存储。
使用 WAL 模式提升并发性能。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import TaskDefinition, ExecutionRecord, TaskStatus, TriggerType
from .config import get_config, resolve_path
from .utils import get_logger, now, format_datetime, parse_datetime, get_node_id

logger = get_logger("scheduler.storage")


class StateStore:
    """SQLite 状态存储

    线程安全的 SQLite 存储封装。
    """

    def __init__(self, db_path: str | Path = None):
        config = get_config()
        self.db_path = Path(db_path) if db_path else resolve_path(config.scheduler.jobstore_path)
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接（单例）"""
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                isolation_level=None,  # 自动提交模式，用事务手动控制
            )
            self._conn.row_factory = sqlite3.Row
            # WAL 模式提升并发性能
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    @contextmanager
    def _transaction(self):
        """事务上下文管理器"""
        conn = self._get_conn()
        with self._lock:
            try:
                conn.execute("BEGIN")
                yield conn
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def _init_db(self):
        """初始化数据库表结构"""
        with self._transaction() as conn:
            # jobs 表：任务定义
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(255) UNIQUE NOT NULL,
                    description TEXT DEFAULT '',
                    trigger_type VARCHAR(20) NOT NULL DEFAULT 'cron',
                    trigger_config TEXT NOT NULL DEFAULT '{}',
                    func_ref VARCHAR(512) NOT NULL,
                    max_retries INTEGER NOT NULL DEFAULT 3,
                    timeout INTEGER NOT NULL DEFAULT 300,
                    idempotency_key_expr VARCHAR(255),
                    status VARCHAR(20) NOT NULL DEFAULT 'active',
                    next_run_time DATETIME,
                    last_run_time DATETIME,
                    params TEXT DEFAULT '{}',
                    version VARCHAR(20) DEFAULT '1.0.0',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # job_executions 表：执行记录
            conn.execute("""
                CREATE TABLE IF NOT EXISTS job_executions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER,
                    run_id VARCHAR(64) UNIQUE NOT NULL,
                    trigger_type VARCHAR(20) NOT NULL DEFAULT 'schedule',
                    scheduled_time DATETIME,
                    start_time DATETIME,
                    end_time DATETIME,
                    duration REAL,
                    status VARCHAR(20) NOT NULL DEFAULT 'running',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    idempotency_key VARCHAR(512),
                    error_code VARCHAR(20),
                    error_message TEXT,
                    result_data TEXT DEFAULT '{}',
                    node_id VARCHAR(128),
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # idempotency_keys 表：幂等键
            conn.execute("""
                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER,
                    idempotency_key VARCHAR(512) UNIQUE NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'success',
                    result_data TEXT DEFAULT '{}',
                    execution_id INTEGER,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # scheduler_state 表：调度器状态
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scheduler_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    state VARCHAR(20) NOT NULL DEFAULT 'stopped',
                    last_heartbeat DATETIME,
                    started_at DATETIME,
                    version VARCHAR(20),
                    node_id VARCHAR(128),
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 索引
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_next_run_time ON jobs(next_run_time)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_exec_job_id ON job_executions(job_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_exec_status ON job_executions(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_exec_start_time ON job_executions(start_time)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_idem_key ON idempotency_keys(idempotency_key)")

            # 初始化调度器状态
            cursor = conn.execute("SELECT COUNT(*) as cnt FROM scheduler_state")
            if cursor.fetchone()["cnt"] == 0:
                conn.execute(
                    "INSERT INTO scheduler_state (id, state, node_id) VALUES (1, 'stopped', ?)",
                    (get_node_id(),)
                )

        logger.info(f"StateStore initialized at {self.db_path}")

    # ─── 任务定义 CRUD ────────────────────────────────────────

    def upsert_job(self, task_def: TaskDefinition) -> int:
        """插入或更新任务定义

        Returns:
            任务 ID
        """
        trigger_config_json = json.dumps(task_def.trigger_config, ensure_ascii=False)
        params_json = json.dumps(task_def.params, ensure_ascii=False)

        with self._transaction() as conn:
            cursor = conn.execute("SELECT id FROM jobs WHERE name = ?", (task_def.name,))
            row = cursor.fetchone()

            if row:
                conn.execute("""
                    UPDATE jobs SET
                        description = ?, trigger_type = ?, trigger_config = ?,
                        func_ref = ?, max_retries = ?, timeout = ?,
                        idempotency_key_expr = ?, status = ?, params = ?,
                        version = ?, updated_at = ?
                    WHERE name = ?
                """, (
                    task_def.description, task_def.trigger_type.value, trigger_config_json,
                    task_def.func_ref, task_def.max_retries, task_def.timeout,
                    task_def.idempotency_key_expr, task_def.status, params_json,
                    task_def.version, now().isoformat(), task_def.name,
                ))
                job_id = row["id"]
            else:
                cursor = conn.execute("""
                    INSERT INTO jobs (
                        name, description, trigger_type, trigger_config,
                        func_ref, max_retries, timeout, idempotency_key_expr,
                        status, params, version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    task_def.name, task_def.description, task_def.trigger_type.value,
                    trigger_config_json, task_def.func_ref, task_def.max_retries,
                    task_def.timeout, task_def.idempotency_key_expr,
                    task_def.status, params_json, task_def.version,
                ))
                job_id = cursor.lastrowid

        logger.debug(f"Job upserted: {task_def.name} (id={job_id})")
        return job_id

    def get_job(self, name: str) -> Optional[TaskDefinition]:
        """根据名称获取任务定义"""
        conn = self._get_conn()
        with self._lock:
            cursor = conn.execute("SELECT * FROM jobs WHERE name = ?", (name,))
            row = cursor.fetchone()

        if not row:
            return None

        return self._row_to_task_def(row)

    def list_jobs(self, status: str = None) -> list[TaskDefinition]:
        """列出所有任务"""
        conn = self._get_conn()
        with self._lock:
            if status:
                cursor = conn.execute("SELECT * FROM jobs WHERE status = ? ORDER BY name", (status,))
            else:
                cursor = conn.execute("SELECT * FROM jobs ORDER BY name")
            rows = cursor.fetchall()

        return [self._row_to_task_def(row) for row in rows]

    def update_job_status(self, name: str, status: str) -> bool:
        """更新任务状态"""
        with self._transaction() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE name = ?",
                (status, now().isoformat(), name)
            )
            return cursor.rowcount > 0

    def update_job_next_run(self, name: str, next_run_time: datetime):
        """更新任务下次执行时间"""
        with self._transaction() as conn:
            conn.execute(
                "UPDATE jobs SET next_run_time = ?, updated_at = ? WHERE name = ?",
                (next_run_time.isoformat(), now().isoformat(), name)
            )

    def _row_to_task_def(self, row: sqlite3.Row) -> TaskDefinition:
        """将数据库行转换为 TaskDefinition"""
        return TaskDefinition(
            name=row["name"],
            func_ref=row["func_ref"],
            trigger_type=TriggerType(row["trigger_type"]),
            trigger_config=json.loads(row["trigger_config"] or "{}"),
            description=row["description"] or "",
            max_retries=row["max_retries"],
            timeout=row["timeout"],
            idempotency_key_expr=row["idempotency_key_expr"] or "{date}",
            status=row["status"],
            params=json.loads(row["params"] or "{}"),
            version=row["version"] or "1.0.0",
        )

    # ─── 执行记录 CRUD ────────────────────────────────────────

    def create_execution(self, record: ExecutionRecord) -> int:
        """创建执行记录

        Returns:
            执行记录 ID
        """
        result_data_json = json.dumps(record.result_data, ensure_ascii=False)

        with self._transaction() as conn:
            # 获取 job_id
            job_id = record.job_id
            if job_id is None and record.task_name:
                cursor = conn.execute("SELECT id FROM jobs WHERE name = ?", (record.task_name,))
                job_row = cursor.fetchone()
                if job_row:
                    job_id = job_row["id"]

            cursor = conn.execute("""
                INSERT INTO job_executions (
                    job_id, run_id, trigger_type, scheduled_time,
                    start_time, status, retry_count, idempotency_key,
                    node_id, result_data
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job_id, record.run_id, record.trigger_type,
                record.scheduled_time.isoformat() if record.scheduled_time else None,
                record.start_time.isoformat() if record.start_time else None,
                record.status, record.retry_count, record.idempotency_key,
                record.node_id or get_node_id(), result_data_json,
            ))
            exec_id = cursor.lastrowid

        logger.debug(f"Execution created: {record.run_id} (id={exec_id})")
        return exec_id

    def update_execution(self, run_id: str, **kwargs) -> bool:
        """更新执行记录"""
        if not kwargs:
            return False

        sets = []
        params = []

        if "status" in kwargs:
            sets.append("status = ?")
            params.append(kwargs["status"])
        if "end_time" in kwargs:
            sets.append("end_time = ?")
            params.append(kwargs["end_time"].isoformat())
        if "duration" in kwargs:
            sets.append("duration = ?")
            params.append(kwargs["duration"])
        if "error_code" in kwargs:
            sets.append("error_code = ?")
            params.append(kwargs["error_code"])
        if "error_message" in kwargs:
            sets.append("error_message = ?")
            params.append(kwargs["error_message"])
        if "result_data" in kwargs:
            sets.append("result_data = ?")
            params.append(json.dumps(kwargs["result_data"], ensure_ascii=False))
        if "retry_count" in kwargs:
            sets.append("retry_count = ?")
            params.append(kwargs["retry_count"])

        if not sets:
            return False

        sets.append("end_time = COALESCE(end_time, ?)" if "end_time" not in kwargs else "")
        params.append(run_id)

        with self._transaction() as conn:
            cursor = conn.execute(
                f"UPDATE job_executions SET {', '.join(s for s in sets if s)} WHERE run_id = ?",
                params,
            )
            return cursor.rowcount > 0

    def get_execution(self, run_id: str) -> Optional[ExecutionRecord]:
        """根据 run_id 获取执行记录"""
        conn = self._get_conn()
        with self._lock:
            cursor = conn.execute("SELECT * FROM job_executions WHERE run_id = ?", (run_id,))
            row = cursor.fetchone()

        if not row:
            return None
        return self._row_to_execution(row)

    def list_executions(self, task_name: str = None, status: str = None,
                        limit: int = 100, offset: int = 0) -> list[ExecutionRecord]:
        """列出执行记录"""
        conn = self._get_conn()
        query = "SELECT je.*, j.name as task_name FROM job_executions je LEFT JOIN jobs j ON je.job_id = j.id"
        conditions = []
        params = []

        if task_name:
            conditions.append("j.name = ?")
            params.append(task_name)
        if status:
            conditions.append("je.status = ?")
            params.append(status)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY je.id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._lock:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

        return [self._row_to_execution(row) for row in rows]

    def _row_to_execution(self, row: sqlite3.Row) -> ExecutionRecord:
        """将数据库行转换为 ExecutionRecord"""
        return ExecutionRecord(
            id=row["id"],
            job_id=row["job_id"],
            run_id=row["run_id"],
            task_name=row["task_name"] if "task_name" in row.keys() else "",
            trigger_type=row["trigger_type"],
            scheduled_time=parse_datetime(row["scheduled_time"], "%Y-%m-%dT%H:%M:%S.%f") if row["scheduled_time"] else None,
            start_time=parse_datetime(row["start_time"], "%Y-%m-%dT%H:%M:%S.%f") if row["start_time"] else None,
            end_time=parse_datetime(row["end_time"], "%Y-%m-%dT%H:%M:%S.%f") if row["end_time"] else None,
            duration=row["duration"] or 0.0,
            status=row["status"],
            retry_count=row["retry_count"],
            idempotency_key=row["idempotency_key"] or "",
            error_code=row["error_code"] or "",
            error_message=row["error_message"] or "",
            result_data=json.loads(row["result_data"] or "{}"),
            node_id=row["node_id"] or "",
            created_at=parse_datetime(row["created_at"], "%Y-%m-%d %H:%M:%S") if row["created_at"] else None,
        )

    # ─── 幂等键操作 ───────────────────────────────────────────

    def check_idempotency(self, idempotency_key: str) -> Optional[dict]:
        """检查幂等键是否已存在成功记录

        Returns:
            存在则返回结果数据，不存在返回 None
        """
        conn = self._get_conn()
        with self._lock:
            cursor = conn.execute(
                "SELECT result_data FROM idempotency_keys WHERE idempotency_key = ? AND status = 'success'",
                (idempotency_key,)
            )
            row = cursor.fetchone()

        if row:
            return json.loads(row["result_data"] or "{}")
        return None

    def record_idempotency(self, job_id: int, idempotency_key: str,
                           result_data: dict, execution_id: int = None,
                           status: str = "success") -> bool:
        """记录幂等键

        Returns:
            是否成功（冲突则返回 False）
        """
        result_json = json.dumps(result_data, ensure_ascii=False)
        try:
            with self._transaction() as conn:
                conn.execute("""
                    INSERT INTO idempotency_keys (
                        job_id, idempotency_key, status, result_data, execution_id
                    ) VALUES (?, ?, ?, ?, ?)
                """, (job_id, idempotency_key, status, result_json, execution_id))
            return True
        except sqlite3.IntegrityError:
            return False

    # ─── 调度器状态 ───────────────────────────────────────────

    def update_scheduler_state(self, state: str, heartbeat: bool = True) -> None:
        """更新调度器状态"""
        with self._transaction() as conn:
            if heartbeat:
                conn.execute(
                    "UPDATE scheduler_state SET state = ?, last_heartbeat = ?, updated_at = ? WHERE id = 1",
                    (state, now().isoformat(), now().isoformat())
                )
            else:
                conn.execute(
                    "UPDATE scheduler_state SET state = ?, updated_at = ? WHERE id = 1",
                    (state, now().isoformat())
                )

    def get_scheduler_state(self) -> dict:
        """获取调度器状态"""
        conn = self._get_conn()
        with self._lock:
            cursor = conn.execute("SELECT * FROM scheduler_state WHERE id = 1")
            row = cursor.fetchone()

        if not row:
            return {"state": "unknown"}

        return {
            "state": row["state"],
            "last_heartbeat": row["last_heartbeat"],
            "started_at": row["started_at"],
            "version": row["version"],
            "node_id": row["node_id"],
            "updated_at": row["updated_at"],
        }

    def mark_started(self, version: str) -> None:
        """标记调度器已启动"""
        with self._transaction() as conn:
            conn.execute("""
                UPDATE scheduler_state
                SET state = 'running', started_at = ?, last_heartbeat = ?,
                    version = ?, node_id = ?, updated_at = ?
                WHERE id = 1
            """, (now().isoformat(), now().isoformat(), version, get_node_id(), now().isoformat()))

    def heartbeat(self) -> None:
        """更新心跳"""
        with self._transaction() as conn:
            conn.execute(
                "UPDATE scheduler_state SET last_heartbeat = ?, updated_at = ? WHERE id = 1",
                (now().isoformat(), now().isoformat())
            )

    # ─── 清理与归档 ───────────────────────────────────────────

    def cleanup_old_executions(self, days: int = 90) -> int:
        """清理过期的执行记录

        Returns:
            清理的记录数
        """
        from datetime import timedelta
        cutoff = now() - timedelta(days=days)

        with self._transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM job_executions WHERE created_at < ? AND status IN ('success', 'failed', 'dlq', 'skipped')",
                (cutoff.isoformat(),)
            )
            count = cursor.rowcount

        if count > 0:
            logger.info(f"Cleaned up {count} old executions (older than {days} days)")
        return count

    def close(self):
        """关闭数据库连接"""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("StateStore connection closed")


# 全局单例
_store: Optional[StateStore] = None


def get_state_store() -> StateStore:
    """获取全局状态存储单例"""
    global _store
    if _store is None:
        _store = StateStore()
    return _store
