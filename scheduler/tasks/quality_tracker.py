"""
质量趋势追踪器

每日记录代码质量指标，提供趋势查询和报告生成。
"""

from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional


class QualityTracker:
    """
    质量指标追踪器

    Usage:
        tracker = QualityTracker(output_dir="~/nightly_reports/")
        tracker.record_daily(files_scanned=10, issues_found=5, security_alerts=1)
        trend = tracker.get_trend(days=7)
    """

    CSV_FILENAME = "quality_trend.csv"
    FIELDS = [
        "date", "files_scanned", "issues_found",
        "blocking_issues", "security_alerts",
        "tests_passed", "tests_failed", "tests_saved",
        "defects_registered", "coverage_rate",
    ]

    def __init__(self, output_dir: str):
        self.output_dir = os.path.expanduser(output_dir)
        os.makedirs(self.output_dir, exist_ok=True)
        self.csv_path = os.path.join(self.output_dir, self.CSV_FILENAME)
        self._ensure_header()

    def _ensure_header(self):
        if not os.path.isfile(self.csv_path):
            with open(self.csv_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=self.FIELDS)
                writer.writeheader()

    def record_daily(
        self,
        files_scanned: int = 0,
        issues_found: int = 0,
        blocking_issues: int = 0,
        security_alerts: int = 0,
        tests_passed: int = 0,
        tests_failed: int = 0,
        tests_saved: int = 0,
        defects_registered: int = 0,
        coverage_rate: str = "",
    ):
        """记录当日质量指标"""
        today = datetime.now().strftime("%Y-%m-%d")
        row = {
            "date": today,
            "files_scanned": files_scanned,
            "issues_found": issues_found,
            "blocking_issues": blocking_issues,
            "security_alerts": security_alerts,
            "tests_passed": tests_passed,
            "tests_failed": tests_failed,
            "tests_saved": tests_saved,
            "defects_registered": defects_registered,
            "coverage_rate": coverage_rate,
        }
        with open(self.csv_path, "a", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.FIELDS)
            writer.writerow(row)

    def get_trend(self, days: int = 7) -> List[Dict]:
        """获取近 N 天的质量趋势"""
        if not os.path.isfile(self.csv_path):
            return []
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        records = []
        with open(self.csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("date", "") >= cutoff:
                    records.append(row)
        return records

    def write_trend_report(self) -> str:
        """生成质量趋势 Markdown 报告"""
        records = self.get_trend(days=30)
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")

        lines = [
            f"# 代码质量趋势报告 — {date_str}", "",
            f"> 数据范围: 近 {len(records)} 天", "",
            "## 趋势数据", "",
            "| 日期 | 扫描文件 | 问题数 | 阻断 | 安全告警 | 测试通过 | 测试失败 | 缺陷登记 |",
            "|------|---------|--------|------|---------|---------|---------|---------|",
        ]
        for r in records[-14:]:  # 最近 14 天
            lines.append(
                f"| {r.get('date', '-')} | {r.get('files_scanned', '0')} "
                f"| {r.get('issues_found', '0')} | {r.get('blocking_issues', '0')} "
                f"| {r.get('security_alerts', '0')} | {r.get('tests_passed', '0')} "
                f"| {r.get('tests_failed', '0')} | {r.get('defects_registered', '0')} |"
            )

        # 趋势分析
        if len(records) >= 2:
            latest = records[-1]
            prev = records[-2]
            issues_delta = int(latest.get("issues_found", 0)) - int(prev.get("issues_found", 0))
            lines.extend([
                "", "## 趋势分析", "",
                f"- 问题数变化: {issues_delta:+d}（{'改善' if issues_delta < 0 else '恶化' if issues_delta > 0 else '持平'}）",
                f"- 安全告警: {latest.get('security_alerts', '0')} 个",
                f"- 测试通过率: {self._calc_pass_rate(latest)}",
            ])

        lines.extend(["", "---", "*由 quality_tracker 自动生成*"])

        report_path = os.path.join(self.output_dir, f"{date_str}_质量趋势报告.md")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return report_path

    @staticmethod
    def _calc_pass_rate(record: Dict) -> str:
        passed = int(record.get("tests_passed", 0))
        failed = int(record.get("tests_failed", 0))
        total = passed + failed
        if total == 0:
            return "N/A"
        return f"{passed * 100 / total:.1f}%"
