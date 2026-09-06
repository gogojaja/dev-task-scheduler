"""Changelog 自动生成任务（v1 — 从 git log 生成 CHANGELOG 条目）

解析 Conventional Commits，按类型分组，追加到各项目的 CHANGELOG.md。
"""
from ..registry import register_task

import json
import os
import re
import subprocess
from datetime import datetime
from typing import Any, Dict, List


# Conventional Commits 类型映射
TYPE_LABELS = {
    "feat": "Features",
    "fix": "Bug Fixes",
    "docs": "Documentation",
    "style": "Styles",
    "refactor": "Code Refactoring",
    "perf": "Performance Improvements",
    "test": "Tests",
    "chore": "Chores",
    "ci": "CI/CD",
}


@register_task(
    name="nightly_changelog_gen",
    trigger="cron", hour=2, minute=15,
    description="nightly_changelog_gen",
)
def run_changelog_gen(config: Dict[str, Any]) -> Dict[str, Any]:
    """生成 Changelog
    
    Args:
        config: 任务配置，包含 target_dirs, output_dir
    
    Returns:
        生成结果摘要
    """
    target_dirs = config.get("target_dirs", [])
    output_dir = os.path.expanduser(config.get("output_dir", "~/projects/TwinForge/docs/nightly_reports/"))
    
    results = []
    for project_dir in target_dirs:
        project_dir = os.path.expanduser(project_dir)
        if not os.path.isdir(project_dir):
            continue
        
        project_name = os.path.basename(project_dir)
        commits = _get_recent_commits(project_dir)
        if not commits:
            continue
        
        # 按类型分组
        grouped = _group_by_type(commits)
        
        # 追加到 CHANGELOG.md
        changelog_path = os.path.join(project_dir, "CHANGELOG.md")
        _append_changelog(changelog_path, project_name, grouped)
        
        results.append({
            "project": project_name,
            "commit_count": len(commits),
            "types": list(grouped.keys()),
        })
    
    report = {
        "task": "changelog_gen",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "projects_updated": len(results),
        "details": results,
    }
    
    _write_report(report, output_dir)
    return report


def _get_recent_commits(project_dir: str) -> List[Dict[str, str]]:
    """获取最近 24 小时的 commits"""
    try:
        cmd = ["git", "-C", project_dir, "log", "--since=24.hours.ago",
               "--format=%H|%h|%s", "--no-merges"]
        output = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=30)
        
        commits = []
        for line in output.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("|", 2)
            if len(parts) == 3:
                commits.append({"hash": parts[0], "short": parts[1], "message": parts[2]})
        return commits
    except Exception:
        return []


def _group_by_type(commits: List[Dict]) -> Dict[str, List[Dict]]:
    """按 Conventional Commits 类型分组"""
    grouped = {}
    pattern = re.compile(r"^(\w+)(?:\(([^)]+)\))?\s*:\s*(.+)")
    
    for commit in commits:
        match = pattern.match(commit["message"])
        if match:
            commit_type = match.group(1).lower()
            scope = match.group(2) or ""
            desc = match.group(3)
            label = TYPE_LABELS.get(commit_type, "Other")
            if label not in grouped:
                grouped[label] = []
            grouped[label].append({"short": commit["short"], "scope": scope, "desc": desc})
        else:
            if "Other" not in grouped:
                grouped["Other"] = []
            grouped["Other"].append({"short": commit["short"], "scope": "", "desc": commit["message"]})
    
    return grouped


def _append_changelog(path: str, project_name: str, grouped: Dict[str, List]):
    """追加 changelog 条目到文件"""
    date_str = datetime.now().strftime("%Y-%m-%d")
    
    lines = [f"\n## [{date_str}]\n\n"]
    for label, items in grouped.items():
        lines.append(f"### {label}\n\n")
        for item in items:
            scope = f"**{item['scope']}**: " if item["scope"] else ""
            lines.append(f"- {scope}{item['desc']} ({item['short']})\n")
        lines.append("\n")
    
    # 追加或创建文件
    with open(path, "a", encoding="utf-8") as f:
        f.writelines(lines)


def _write_report(report: Dict, output_dir: str):
    """写入报告"""
    os.makedirs(output_dir, exist_ok=True)
    date_str = report["date"]
    
    json_path = os.path.join(output_dir, f"{date_str}_changelog_gen.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    md_path = os.path.join(output_dir, f"{date_str}_changelog_gen.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Changelog 生成报告 — {date_str}\n\n")
        f.write(f"- 更新项目: {report['projects_updated']} 个\n\n")
        for d in report["details"]:
            f.write(f"### {d['project']}\n")
            f.write(f"- Commits: {d['commit_count']}\n")
            f.write(f"- 类型: {', '.join(d['types'])}\n\n")
    
    print(f"[changelog_gen] 报告: {md_path}")
