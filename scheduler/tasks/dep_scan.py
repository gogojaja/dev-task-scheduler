"""依赖漏洞扫描任务（v1 — 使用 pip-audit 扫描 Python 依赖）

扫描 requirements.txt 中的已知 CVE 漏洞。
"""
from ..registry import register_task

import json
import os
import subprocess
from datetime import datetime
from typing import Any, Dict, List


@register_task(
    name="nightly_dep_scan",
    trigger="cron", day_of_week="wed", hour=4, minute=0,
    description="nightly_dep_scan",
)
def run_dep_scan(context) -> Dict[str, Any]:
    """执行依赖漏洞扫描
    
    Args:
        context: TaskContext 执行上下文
    
    Returns:
        扫描结果摘要
    """
    target_dirs = context.params.get("target_dirs", [])
    output_dir = os.path.expanduser(context.params.get("output_dir", "~/projects/TwinForge/docs/nightly_reports/"))
    
    results = []
    for project_dir in target_dirs:
        project_dir = os.path.expanduser(project_dir)
        if not os.path.isdir(project_dir):
            continue
        
        project_name = os.path.basename(project_dir)
        
        # 查找 requirements 文件
        req_files = _find_requirements(project_dir)
        for req_file in req_files:
            vulns = _scan_requirements(req_file)
            if vulns:
                results.append({
                    "project": project_name,
                    "requirements_file": os.path.relpath(req_file, project_dir),
                    "vuln_count": len(vulns),
                    "vulnerabilities": vulns,
                })
    
    report = {
        "task": "dep_scan",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "total_vulns": sum(r["vuln_count"] for r in results),
        "details": results,
    }
    
    _write_report(report, output_dir)
    return report


def _find_requirements(project_dir: str) -> List[str]:
    """查找 requirements 文件"""
    candidates = [
        os.path.join(project_dir, "requirements.txt"),
        os.path.join(project_dir, "requirements", "requirements.txt"),
    ]
    # 也搜索 setup.py / pyproject.toml
    for f in ["setup.py", "pyproject.toml"]:
        path = os.path.join(project_dir, f)
        if os.path.exists(path):
            candidates.append(path)
    
    return [c for c in candidates if os.path.exists(c)]


def _scan_requirements(req_file: str) -> List[Dict]:
    """使用 pip-audit 扫描依赖漏洞"""
    try:
        cmd = ["python", "-m", "pip_audit", "-r", req_file, "--format=json"]
        output = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=120)
        
        data = json.loads(output) if output.strip() else {}
        vulns = []
        for dep in data.get("dependencies", []):
            for vuln in dep.get("vulns", []):
                vulns.append({
                    "package": dep.get("name", ""),
                    "version": dep.get("version", ""),
                    "id": vuln.get("id", ""),
                    "description": vuln.get("description", "")[:200],
                    "fix_versions": vuln.get("fix_versions", []),
                })
        return vulns
    except FileNotFoundError:
        # pip-audit 未安装，使用 safety fallback
        return _safety_fallback(req_file)
    except Exception as e:
        print(f"[dep_scan] pip-audit error: {e}")
        return []


def _safety_fallback(req_file: str) -> List[Dict]:
    """safety 作为 fallback"""
    try:
        cmd = ["python", "-m", "safety", "check", "--file", req_file, "--json"]
        output = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=120)
        
        items = json.loads(output) if output.strip() else []
        return [
            {
                "package": item[3] if len(item) > 3 else "",
                "version": item[2] if len(item) > 2 else "",
                "id": item[4] if len(item) > 4 else "",
                "description": item[5] if len(item) > 5 else "",
                "fix_versions": [],
            }
            for item in items
        ]
    except Exception:
        return []


def _write_report(report: Dict, output_dir: str):
    """写入报告"""
    os.makedirs(output_dir, exist_ok=True)
    date_str = report["date"]
    
    json_path = os.path.join(output_dir, f"{date_str}_dep_scan.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    md_path = os.path.join(output_dir, f"{date_str}_dep_scan.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# 依赖漏洞扫描报告 — {date_str}\n\n")
        f.write(f"- 发现漏洞: {report['total_vulns']} 个\n\n")
        if report["details"]:
            for d in report["details"]:
                f.write(f"## {d['project']} — {d['requirements_file']}\n\n")
                f.write(f"| 包名 | 版本 | CVE | 修复版本 |\n")
                f.write(f"|------|------|-----|----------|\n")
                for v in d["vulnerabilities"]:
                    fix = ", ".join(v["fix_versions"]) if v["fix_versions"] else "未知"
                    f.write(f"| {v['package']} | {v['version']} | {v['id']} | {fix} |\n")
                f.write("\n")
    
    print(f"[dep_scan] 报告: {md_path}")
