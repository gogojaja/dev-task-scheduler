"""
夜间任务报告生成器

生成 JSON + Markdown 双格式报告。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import List, Dict, Any, Optional


class NightlyReportWriter:
    """
    报告生成器

    Usage:
        writer = NightlyReportWriter(output_dir="~/reports/")
        path = writer.write(
            task_name="code_review",
            results=[...],
            metadata={"files_count": 8, "duration_s": 272},
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
        """
        生成报告（JSON + Markdown）

        Args:
            task_name: 任务名称（code_review / code_completion / test_generation）
            results: 每个文件的处理结果列表
            metadata: 额外元数据

        Returns:
            报告文件路径（Markdown）
        """
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
        json_path = os.path.join(
            self.output_dir, f"{date_str}_{task_name}.json"
        )
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False, default=str)

        # Markdown 报告
        md_path = os.path.join(
            self.output_dir, f"{date_str}_{task_name}.md"
        )
        md_content = self._render_markdown(task_name, date_str, time_str, results, metadata)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        return md_path

    def _render_markdown(
        self,
        task_name: str,
        date_str: str,
        time_str: str,
        results: List[Dict],
        metadata: Optional[Dict],
    ) -> str:
        """渲染 Markdown 报告"""
        title_map = {
            "code_review": "代码走查",
            "code_completion": "代码补全",
            "test_generation": "测试生成",
        }
        title = title_map.get(task_name, task_name)
        meta = metadata or {}

        lines = [
            f"# TwinForge 夜间{title}报告 — {date_str}",
            "",
            f"> 生成时间: {date_str} {time_str}",
            "",
            "## 摘要",
            "",
        ]

        if task_name == "code_review":
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
            lines.extend([
                f"- 扫描文件: {len(results)} 个",
                f"- 发现问题: {total_issues} 个（{blocking} 阻断 / {major} 主要 / {minor} 次要）",
                f"- 耗时: {meta.get('duration_s', 0):.0f}s",
                "",
            ])
            if total_issues > 0:
                lines.append("## 问题详情")
                lines.append("")
                for r in results:
                    if r.get("issues"):
                        lines.append(f"### {r.get('file_path', 'unknown')}")
                        lines.append("")
                        lines.append("| 行号 | 严重级 | 描述 | 建议 |")
                        lines.append("|------|--------|------|------|")
                        for issue in r["issues"]:
                            lines.append(
                                f"| {issue.get('line', 0)} "
                                f"| {issue.get('severity', '')} "
                                f"| {issue.get('description', '')} "
                                f"| {issue.get('suggestion', '')} |"
                            )
                        lines.append("")

        elif task_name == "test_generation":
            lines.extend([
                f"- 处理文件: {len(results)} 个",
                f"- 生成测试: {sum(1 for r in results if r.get('test_code'))} 个",
                f"- 耗时: {meta.get('duration_s', 0):.0f}s",
                "",
            ])
            for r in results:
                status = "OK" if r.get("test_code") else "FAIL"
                lines.append(f"- {r.get('file_path', '?')}: {status}")
            lines.append("")

        elif task_name == "code_completion":
            lines.extend([
                f"- 处理文件: {len(results)} 个",
                f"- 生成建议: {sum(1 for r in results if r.get('completion_code'))} 个",
                f"- 耗时: {meta.get('duration_s', 0):.0f}s",
                "",
            ])

        # 错误汇总
        errors = [r for r in results if r.get("error")]
        if errors:
            lines.append("## 错误")
            lines.append("")
            for r in errors:
                lines.append(f"- {r.get('file_path', '?')}: {r['error']}")
            lines.append("")

        lines.append("---")
        lines.append(f"*由 dev-model-router + dev-task-scheduler 自动生成*")
        return "\n".join(lines)
