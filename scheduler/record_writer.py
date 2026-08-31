"""
CSV 执行记录写入器

负责将执行记录写入 CSV 台账文件，格式与现有台账一致（UTF-8 with BOM）。
采用追加写入模式，保证审计可追溯。
"""

from __future__ import annotations

import csv
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import ExecutionRecord, TaskStatus
from .config import get_config, resolve_path
from .utils import get_logger, format_datetime, gen_execution_id, today_str, get_node_id, safe_truncate

logger = get_logger("scheduler.recording")


# CSV 列定义（与台账格式一致）
CSV_COLUMNS = [
    "执行编号",
    "任务名称",
    "运行ID",
    "触发方式",
    "计划时间",
    "开始时间",
    "结束时间",
    "耗时(秒)",
    "状态",
    "重试次数",
    "幂等键",
    "错误码",
    "错误信息",
    "执行节点",
    "记录时间",
]


class RecordWriter:
    """CSV 执行记录写入器

    追加写入模式，文件不存在则创建（含 BOM 和表头）。
    """

    def __init__(self, csv_path: str | Path = None):
        config = get_config()
        self.csv_path = Path(csv_path) if csv_path else resolve_path(config.recording.csv_path)
        self.enabled = config.recording.enabled
        self._ensure_file()

    def _ensure_file(self):
        """确保 CSV 文件存在，不存在则创建"""
        if not self.enabled:
            return

        self.csv_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.csv_path.exists():
            # 新建文件，写入 BOM 和表头
            with open(self.csv_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(CSV_COLUMNS)
            logger.info(f"Created execution record file: {self.csv_path}")

    def _record_to_row(self, record: ExecutionRecord, exec_no: str = None) -> list[str]:
        """将 ExecutionRecord 转换为 CSV 行"""
        return [
            exec_no or gen_execution_id(),
            record.task_name,
            record.run_id,
            record.trigger_type,
            format_datetime(record.scheduled_time),
            format_datetime(record.start_time),
            format_datetime(record.end_time),
            f"{record.duration:.2f}" if record.duration else "",
            record.status,
            str(record.retry_count),
            record.idempotency_key,
            record.error_code,
            safe_truncate(record.error_message, 500),
            record.node_id or get_node_id(),
            format_datetime(datetime.now()),
        ]

    def append_record(self, record: ExecutionRecord) -> Optional[str]:
        """追加一条执行记录

        Args:
            record: 执行记录

        Returns:
            执行编号，失败返回 None
        """
        if not self.enabled:
            return None

        try:
            exec_no = gen_execution_id()
            row = self._record_to_row(record, exec_no)

            with open(self.csv_path, "a", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(row)

            logger.debug(f"Execution record appended: {record.run_id} -> {exec_no}")
            return exec_no

        except Exception as e:
            logger.error(f"Failed to append execution record: {e}")
            return None

    def append_batch(self, records: list[ExecutionRecord]) -> list[str]:
        """批量追加执行记录

        Returns:
            执行编号列表
        """
        if not self.enabled or not records:
            return []

        exec_nos = []
        try:
            with open(self.csv_path, "a", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                for record in records:
                    exec_no = gen_execution_id()
                    writer.writerow(self._record_to_row(record, exec_no))
                    exec_nos.append(exec_no)

            logger.debug(f"Batch appended {len(records)} execution records")
        except Exception as e:
            logger.error(f"Failed to batch append execution records: {e}")

        return exec_nos

    def get_record_count(self) -> int:
        """获取记录总数"""
        if not self.csv_path.exists():
            return 0

        try:
            with open(self.csv_path, "r", encoding="utf-8-sig") as f:
                # 减去表头行
                return sum(1 for _ in f) - 1
        except Exception:
            return 0

    def get_today_stats(self) -> dict:
        """获取今日执行统计"""
        if not self.csv_path.exists():
            return {"total": 0, "success": 0, "failed": 0, "dlq": 0}

        today = today_str()
        stats = {"total": 0, "success": 0, "failed": 0, "dlq": 0}

        try:
            with open(self.csv_path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    start_time = row.get("开始时间", "")
                    if start_time.startswith(today):
                        stats["total"] += 1
                        status = row.get("状态", "")
                        if status == "success":
                            stats["success"] += 1
                        elif status == "failed":
                            stats["failed"] += 1
                        elif status == "dlq":
                            stats["dlq"] += 1
        except Exception as e:
            logger.error(f"Failed to get today stats: {e}")

        return stats


# 全局单例
_writer: Optional[RecordWriter] = None


def get_record_writer() -> RecordWriter:
    """获取全局记录写入器单例"""
    global _writer
    if _writer is None:
        _writer = RecordWriter()
    return _writer
