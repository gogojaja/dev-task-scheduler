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
        return self.get_stats_by_date(today_str())

    def get_stats_by_date(self, date_str: str) -> dict:
        """获取指定日期的执行统计

        Args:
            date_str: 日期字符串 YYYY-MM-DD

        Returns:
            统计字典
        """
        if not self.csv_path.exists():
            return {"total": 0, "success": 0, "failed": 0, "dlq": 0}

        stats = {"total": 0, "success": 0, "failed": 0, "dlq": 0}

        try:
            with open(self.csv_path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    start_time = row.get("开始时间", "")
                    if start_time.startswith(date_str):
                        stats["total"] += 1
                        status = row.get("状态", "")
                        if status == "success":
                            stats["success"] += 1
                        elif status == "failed":
                            stats["failed"] += 1
                        elif status == "dlq":
                            stats["dlq"] += 1
        except Exception as e:
            logger.error(f"Failed to get stats for {date_str}: {e}")

        return stats

    # ─── CSV 轮转 ───────────────────────────────────────────

    def rotate_if_needed(self, max_size_mb: float = 10.0) -> Optional[Path]:
        """如果当前 CSV 文件超过大小限制，轮转为归档文件

        轮转规则：
        - 当前文件重命名为 {name}_{date}.csv
        - 创建新文件（含 BOM 和表头）

        Args:
            max_size_mb: 最大文件大小（MB）

        Returns:
            归档文件路径，未轮转返回 None
        """
        if not self.csv_path.exists():
            return None

        size_mb = self.csv_path.stat().st_size / (1024 * 1024)
        if size_mb < max_size_mb:
            return None

        # 生成归档文件名
        date_str = today_str().replace("-", "")
        archive_name = f"{self.csv_path.stem}_{date_str}{self.csv_path.suffix}"
        archive_path = self.csv_path.parent / archive_name

        # 如果归档文件已存在，加序号
        counter = 1
        while archive_path.exists():
            archive_name = f"{self.csv_path.stem}_{date_str}_{counter}{self.csv_path.suffix}"
            archive_path = self.csv_path.parent / archive_name
            counter += 1

        try:
            # 重命名当前文件为归档
            self.csv_path.rename(archive_path)

            # 创建新文件
            self._ensure_file()

            logger.info(f"CSV rotated: {self.csv_path.name} -> {archive_path.name} ({size_mb:.1f}MB)")
            return archive_path

        except Exception as e:
            logger.error(f"CSV rotation failed: {e}")
            return None

    def list_archives(self) -> list[dict]:
        """列出所有归档文件

        Returns:
            归档文件信息列表
        """
        archives = []
        parent = self.csv_path.parent
        stem = self.csv_path.stem

        if not parent.exists():
            return archives

        for f in sorted(parent.iterdir()):
            if f.name.startswith(stem + "_") and f.suffix == self.csv_path.suffix and f != self.csv_path:
                archives.append({
                    "path": str(f),
                    "name": f.name,
                    "size_mb": round(f.stat().st_size / (1024 * 1024), 2),
                    "records": sum(1 for _ in open(f, encoding="utf-8-sig")) - 1,
                })

        return archives

    # ─── 查询接口 ───────────────────────────────────────────

    def query_records(
        self,
        task_name: str = None,
        status: str = None,
        date_from: str = None,
        date_to: str = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """查询执行记录

        Args:
            task_name: 按任务名过滤
            status: 按状态过滤
            date_from: 开始日期 YYYY-MM-DD
            date_to: 结束日期 YYYY-MM-DD
            limit: 最大返回数
            offset: 偏移量

        Returns:
            记录字典列表
        """
        if not self.csv_path.exists():
            return []

        results = []
        skipped = 0

        try:
            with open(self.csv_path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # 任务名过滤
                    if task_name and row.get("任务名称") != task_name:
                        continue
                    # 状态过滤
                    if status and row.get("状态") != status:
                        continue
                    # 日期范围过滤
                    start_time = row.get("开始时间", "")
                    if date_from and start_time < date_from:
                        continue
                    if date_to and start_time > date_to + " 23:59:59":
                        continue

                    # 偏移
                    if skipped < offset:
                        skipped += 1
                        continue

                    results.append(dict(row))

                    if len(results) >= limit:
                        break

        except Exception as e:
            logger.error(f"Query records failed: {e}")

        return results


# 全局单例
_writer: Optional[RecordWriter] = None


def get_record_writer() -> RecordWriter:
    """获取全局记录写入器单例"""
    global _writer
    if _writer is None:
        _writer = RecordWriter()
    return _writer
