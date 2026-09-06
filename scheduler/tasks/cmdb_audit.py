"""CMDB 一致性审计任务（每周日执行）

扫描实际 Git 仓库/Tailscale 节点/Ollama 模型/台账时效，
与 TwinForge 台账对比，输出 DRIFT 报告 + 更新建议。

审计项（4 类）：
  1. Git 仓库清单：实际 .git 目录 vs docs/repos/*.md
  2. Tailscale 节点：tailscale status vs docs/nodes/README.md
  3. Ollama 模型：/api/tags vs docs/nodes/mac-mini.md
  4. 台账时效性：最后更新日期 vs 阈值（默认 30 天）

依赖：TwinForge/scripts/cmdb_audit.py（纯工具脚本，无 LLM 依赖）
"""

import importlib.util
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


def run_cmdb_audit(config: Dict[str, Any]) -> Dict[str, Any]:
    """执行 CMDB 一致性审计

    Args:
        config: 任务配置，支持以下键：
            - output_dir: 报告输出目录
            - twinforge_root: TwinForge 项目根目录
            - audit.staleness_days: 台账过期阈值（天）

    Returns:
        审计结果摘要
    """
    output_dir = os.path.expanduser(
        config.get("output_dir", "~/projects/TwinForge/docs/nightly_reports/")
    )
    twinforge_root = os.path.expanduser(
        config.get("twinforge_root", "~/projects/TwinForge")
    )
    audit_cfg = config.get("audit", {})
    staleness_days = audit_cfg.get("staleness_days", 30)

    # 导入 TwinForge 的 cmdb_audit 模块
    audit_script = Path(twinforge_root) / "scripts" / "cmdb_audit.py"
    if not audit_script.exists():
        alt_path = Path(os.path.expanduser("~/projects/TwinForge/scripts/cmdb_audit.py"))
        if alt_path.exists():
            audit_script = alt_path
        else:
            return _error_result(f"cmdb_audit.py not found: {audit_script}")

    # 动态加载模块
    try:
        spec = importlib.util.spec_from_file_location("cmdb_audit", audit_script)
        if spec is None or spec.loader is None:
            return _error_result(f"Cannot load module: {audit_script}")
        cmdb_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cmdb_mod)
    except Exception as e:
        return _error_result(f"Module load failed: {e}")

    # 执行审计
    try:
        report = cmdb_mod.run_audit(
            twinforge_root=Path(twinforge_root),
            output_dir=Path(output_dir),
            staleness_days=staleness_days,
        )
    except Exception as e:
        return _error_result(f"Audit failed: {e}")

    # 生成 Markdown 摘要报告
    _write_md_report(report, output_dir)

    # 返回任务调度器需要的摘要
    summary = report.get("summary", {})
    return {
        "task": "cmdb_audit",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "total_checks": summary.get("total_checks", 0),
        "match": summary.get("match", 0),
        "drift": summary.get("drift", 0),
        "missing": summary.get("missing", 0),
        "suggestions_count": len(report.get("suggestions", [])),
        "report_path": str(Path(output_dir) / f"cmdb_audit_{datetime.now().strftime('%Y%m%d')}.json"),
        "status": "PASS" if summary.get("drift", 0) == 0 and summary.get("missing", 0) == 0 else "DRIFT",
    }


def _error_result(message: str) -> Dict[str, Any]:
    """返回错误结果"""
    print(f"[cmdb_audit] ERROR: {message}")
    return {
        "task": "cmdb_audit",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "status": "ERROR",
        "error": message,
        "total_checks": 0,
        "match": 0,
        "drift": 0,
        "missing": 0,
        "suggestions_count": 0,
    }


def _write_md_report(report: Dict[str, Any], output_dir: str):
    """生成 Markdown 格式审计摘要报告"""
    os.makedirs(output_dir, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    md_path = os.path.join(output_dir, f"{date_str}_cmdb_audit.md")

    summary = report.get("summary", {})
    checks = report.get("checks", [])
    suggestions = report.get("suggestions", [])

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# CMDB Audit Report - {date_str}\n\n")
        f.write(f"- Version: {report.get('audit_version', 'unknown')}\n")
        f.write(f"- Time: {report.get('audit_time', 'unknown')}\n")
        f.write(f"- Root: {report.get('twinforge_root', 'unknown')}\n")
        f.write(f"- Staleness threshold: {report.get('staleness_threshold_days', 30)} days\n\n")

        f.write("## Summary\n\n")
        f.write(f"| Metric | Value |\n")
        f.write(f"|--------|-------|\n")
        f.write(f"| Total checks | {summary.get('total_checks', 0)} |\n")
        f.write(f"| MATCH | {summary.get('match', 0)} |\n")
        f.write(f"| DRIFT | {summary.get('drift', 0)} |\n")
        f.write(f"| MISSING | {summary.get('missing', 0)} |\n\n")

        drift_count = summary.get("drift", 0)
        missing_count = summary.get("missing", 0)
        if drift_count == 0 and missing_count == 0:
            f.write("**Result: PASS - CMDB is consistent with actual state.**\n\n")
        else:
            f.write(f"**Result: DRIFT - {drift_count} drifts + {missing_count} missing, manual review needed.**\n\n")

        f.write("## Details\n\n")
        f.write("| Type | Target | Expected | Actual | Status |\n")
        f.write("|------|--------|----------|--------|--------|\n")
        for c in checks:
            icon = {"MATCH": "OK", "DRIFT": "WARN", "MISSING": "FAIL"}.get(c.get("status", ""), "?")
            f.write(f"| {c.get('type', '')} | {c.get('target', '')} | {c.get('expected', '')} | {c.get('actual', '')} | {icon} |\n")

        drifts = [c for c in checks if c.get("status") == "DRIFT"]
        if drifts:
            f.write("\n## Drift Details\n\n")
            for c in drifts:
                f.write(f"### {c.get('target', 'unknown')}\n\n")
                f.write(f"- Issue: {c.get('detail', '')}\n")
                if c.get("suggestion"):
                    f.write(f"- Suggestion: {c['suggestion']}\n")
                f.write("\n")

        if suggestions:
            f.write("## Suggestions\n\n")
            for i, s in enumerate(suggestions, 1):
                f.write(f"{i}. {s}\n")

    print(f"[cmdb_audit] MD report: {md_path}")