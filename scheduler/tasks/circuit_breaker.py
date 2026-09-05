"""熔断器模块（v1 — 四层保护之 Layer 3）

任务连续失败 >= 阈值天数时自动禁用（熔断），每日试探恢复。
状态机：CLOSED → (连续失败>=阈值) → OPEN → (次日) → HALF_OPEN → (成功) → CLOSED
                                                      → (失败) → OPEN
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import datetime, date
from typing import Dict, List, Optional


STATE_CLOSED = "closed"
STATE_OPEN = "open"
STATE_HALF_OPEN = "half_open"


@dataclass
class BreakerState:
    task_name: str
    state: str = STATE_CLOSED
    consecutive_failures: int = 0
    last_failure_date: str = ""
    last_success_date: str = ""
    detail: str = ""


class CircuitBreaker:
    """任务级熔断器
    
    Usage:
        cb = CircuitBreaker(csv_path="~/nightly_reports/circuit_breaker.csv")
        if cb.should_execute("nightly_dead_code"):
            try:
                run_task()
                cb.record_success("nightly_dead_code")
            except Exception:
                cb.record_failure("nightly_dead_code", "task error")
        else:
            print("task is circuit-broken, skipping")
    """

    def __init__(self, csv_path: str, failure_threshold: int = 3):
        self.csv_path = os.path.expanduser(csv_path)
        self.failure_threshold = failure_threshold
        self._states: Dict[str, BreakerState] = {}
        self._load()

    def _load(self):
        """从 CSV 加载状态"""
        if not os.path.isfile(self.csv_path):
            return
        try:
            with open(self.csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    name = row.get("task_name", "")
                    if name:
                        self._states[name] = BreakerState(
                            task_name=name,
                            state=row.get("state", STATE_CLOSED),
                            consecutive_failures=int(row.get("consecutive_failures", 0)),
                            last_failure_date=row.get("last_failure_date", ""),
                            last_success_date=row.get("last_success_date", ""),
                            detail=row.get("detail", ""),
                        )
        except Exception:
            pass

    def _save(self):
        """保存状态到 CSV"""
        os.makedirs(os.path.dirname(self.csv_path) or ".", exist_ok=True)
        with open(self.csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["task_name", "state", "consecutive_failures",
                             "last_failure_date", "last_success_date", "detail"])
            for s in self._states.values():
                writer.writerow([s.task_name, s.state, s.consecutive_failures,
                                 s.last_failure_date, s.last_success_date, s.detail])

    def _get_state(self, task_name: str) -> BreakerState:
        if task_name not in self._states:
            self._states[task_name] = BreakerState(task_name=task_name)
        return self._states[task_name]

    def should_execute(self, task_name: str) -> bool:
        """判断任务是否应该执行（未被熔断）
        
        每日首次检查时自动将 OPEN 转为 HALF_OPEN（允许试探一次）。
        """
        bs = self._get_state(task_name)
        today = date.today().isoformat()

        if bs.state == STATE_CLOSED:
            return True

        if bs.state == STATE_OPEN:
            # 如果上次失败不是今天，转为 half_open 允许试探
            if bs.last_failure_date != today:
                bs.state = STATE_HALF_OPEN
                bs.detail = "daily recovery probe"
                self._save()
                return True
            return False

        if bs.state == STATE_HALF_OPEN:
            # 允许试探
            return True

        return True

    def record_success(self, task_name: str):
        """记录任务成功 → 恢复 CLOSED"""
        bs = self._get_state(task_name)
        bs.state = STATE_CLOSED
        bs.consecutive_failures = 0
        bs.last_success_date = date.today().isoformat()
        bs.detail = ""
        self._save()

    def record_failure(self, task_name: str, detail: str = ""):
        """记录任务失败 → 累计连续失败次数，超阈值则熔断"""
        bs = self._get_state(task_name)
        today = date.today().isoformat()
        bs.consecutive_failures += 1
        bs.last_failure_date = today
        bs.detail = detail[:200]

        if bs.consecutive_failures >= self.failure_threshold:
            bs.state = STATE_OPEN
            bs.detail = f"circuit open after {bs.consecutive_failures} failures: {detail[:100]}"
        
        self._save()

    def get_all_states(self) -> List[BreakerState]:
        """获取所有任务状态"""
        return list(self._states.values())

    def get_open_breakers(self) -> List[BreakerState]:
        """获取所有已熔断的任务"""
        return [s for s in self._states.values() if s.state == STATE_OPEN]
