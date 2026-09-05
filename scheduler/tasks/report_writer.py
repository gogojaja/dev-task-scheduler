"""
夜间任务报告生成器（v2 — 安全扫描 + 测试执行 + 质量趋势）

生成 JSON + Markdown 双格式报告。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import List, Dict, Any, Optional


class NightlyReportWriter:
    """
    报告生成器（v2）

    Usage:
        writer = NightlyReportWriter(output_dir="~/reports/")
        path = writer.write(
            task_name="code_review",
            results=[...],
            metadata={"files_count": 8, "security_alerts": 2},
        )
    """

    def __init__(self, output_dir: str):
        self.output_dir = os.path.expanduser(output_dir)
        os.makedirs(self.output_dir, exist_ok=True)

    def write(
        self,
        task_name: str,
        results: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")

        report_data = {
            "task_name": task_name,
            "date": date_str,
            "time": time_str,
            "results": results,
            "metadata": metadata or {},
        }

        # JSON 报告
        json_path = os.path.join(self.output_dir, f"{date_str}_{task_name}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False, default=str)

        # Markdown 报告
        md_path = os.path.join(self.output_dir, f"{date_str}_{task_name}.md")
        md_content = self._render_markdown(task_name, date_str, time_str, results, metadata)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        return md_path

    def _render_markdown(
        self, task_name: str, date_str: str, time_str: str,
        results: List[Dict], metadata: Optional[Dict],
    ) -> str:
        title_map = {
            "code_review": "代码走查",
            "code_completion": "代码补全",
            "test_generation": "测试生成",
        }
        title = title_map.get(task_name, task_name)
        meta = metadata or {}

        lines = [
            f"# TwinForge 夜间{title}报告 — {date_str}", "",
            f"> 生成时间: {date_str} {time_str}", "",
            "## 摘要", "",
        ]

        if task_name == "code_review":
            lines.extend(self._render_code_review(date_str, results, meta))
        elif task_name == "test_generation":
            lines.extend(self._render_test_generation(results, meta))
        elif task_name == "code_completion":
            lines.extend(self._render_code_completion(results, meta))

        # 错误汇总
        errors = [r for r in results if r.get("error")]
        if errors:
            lines.extend(["", "## 错误", ""])
            for r in errors:
                lines.append(f"- {r.get('file_path', '?')}: {r['error']}")

        lines.extend(["", "---", "*由 dev-model-router + dev-task-scheduler 自动生成*"])
        return "\n".join(lines)

    def _render_code_review(self, date_str, results, meta):
        total_issues = sum(len(r.get("issues", [])) for r in results)
        blocking = sum(
            sum(1 for i in r.get("issues", []) if i.get("severity") == "阻断")
            for r in results
        )
        major = sum(
            sum(1 for i in r.get("issues", []) if i.get("severity") == "主要")
            for r in results
        )
        minor = sum(
            sum(1 for i in r.get("issues", []) if i.get("severity") == "次要")
            for r in results
        )
        security_alerts = meta.get("security_alerts", 0)
        incremental = meta.get("incremental", False)

        lines = [
            f"- 扫描文件: {len(results)} 个"
            + (f"（增量模式，总计 {meta.get('total_files', '?')} 个）" if incremental else ""),
            f"- 发现问题: {total_issues} 个（{blocking} 阻断 / {major} 主要 / {minor} 次要）",
            f"- 安全告警: {security_alerts} 个",
            f"- 缺陷登记: {meta.get('defects_registered', 0)} 个",
            f"- 耗时: {meta.get('duration_s', 0):.0f}s", "",
        ]

        # 安全扫描结果
        if security_alerts > 0:
            lines.append("## 安全扫描告警")
            lines.append("")
            for r in results:
                if r.get("security_findings"):
                    lines.append(f"### {r.get('file_path', 'unknown')}")
                    lines.append("")
                    for sf in r["security_findings"]:
                        lines.append(f"- 行 {sf.get('line', 0)}: "
                                     f"[{sf.get('severity', '?')}] {sf.get('description', '')}")
                    lines.append("")

        # 问题详情
        if total_issues > 0:
            lines.append("## 问题详情")
            lines.append("")
            for r in results:
                if r.get("issues"):
                    lines.append(f"### {r.get('file_path', 'unknown')}")
                    lines.append("")
                    lines.append("| 行号 | 严重级 | 类别 | 描述 | 可自动修复 |")
                    lines.append("|------|--------|------|------|-----------|")
                    for issue in r["issues"]:
                        rep = "Yes" if issue.get("repeatable") else "No"
                        lines.append(
                            f"| {issue.get('line', 0)} | {issue.get('severity', '')} "
                            f"| {issue.get('category', '')} "
                            f"| {issue.get('description', '')[:60]} | {rep} |"
                        )
                    lines.append("")
        return lines

    def _render_test_generation(self, results, meta):
        passed = meta.get("tests_passed", 0)
        failed = meta.get("tests_failed", 0)
        saved = meta.get("tests_saved", 0)
        coverage = meta.get("coverage", {})

        lines = [
            f"- 处理文件: {len(results)} 个",
            f"- 测试通过: {passed} 个 / 失败: {failed} 个 / 已保存: {saved} 个",
            f"- 耗时: {meta.get('duration_s', 0):.0f}s", "",
        ]

        # 测试执行结果
        lines.append("## 测试执行结果")
        lines.append("")
        lines.append("| 文件 | 测试数 | 结果 | 保存位置 |")
        lines.append("|------|--------|------|---------|")
        for r in results:
            status = r.get("test_result", "N/A")
            icon = {"PASS": "PASS", "FAIL": "FAIL", "生成失败": "ERR"}.get(status, "?")
            target_count = r.get("test_count", len(r.get("test_targets", [])))
            saved_to = r.get("saved_to", "-")
            lines.append(f"| {r.get('file_path', '?')[:50]} | {target_count} | {icon} | {saved_to} |")
        lines.append("")

        # 覆盖率
        if coverage:
            lines.append("## 覆盖率")
            lines.append("")
            lines.append("| 项目 | 覆盖率 | 总行数 | 已覆盖 |")
            lines.append("|------|--------|--------|--------|")
            for name, data in coverage.items():
                lines.append(f"| {name} | {data.get('rate', '-')} "
                             f"| {data.get('total', 0)} | {data.get('covered', 0)} |")
            lines.append("")
        return lines

    def _render_code_completion(self, results, meta):
        total_markers = meta.get("total_markers", 0)
        lines = [
            f"- 处理文件: {len(results)} 个",
            f"- TODO/FIXME 标记: {total_markers} 个",
            f"- 耗时: {meta.get('duration_s', 0):.0f}s", "",
        ]
        if results:
            lines.append("## 补全建议")
            lines.append("")
            for r in results:
                if r.get("completion_code"):
                    conf = r.get("confidence", 0)
                    conf_icon = "high" if conf >= 0.8 else "medium" if conf >= 0.5 else "low"
                    lines.append(f"### {r.get('file_path', '?')} (行 {r.get('marker_line', 0)}, "
                                 f"{r.get('marker_type', '?')}, 置信度: {conf_icon})")
                    lines.append(f"> {r.get('marker_comment', '')}")
                    lines.append("")
        return lines
