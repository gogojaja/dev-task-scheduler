"""死代码检测任务（v1 — 使用 vulture 检测未使用的 Python 代码）

扫描项目中的未使用函数/类/变量/导入。
"""
from ..registry import register_task

import json
import os
import subprocess
from datetime import datetime
from typing import Any, Dict, List


@register_task(
    name="nightly_dead_code",
    trigger="cron", day_of_week="mon", hour=4, minute=0,
    description="nightly_dead_code",
)
def run_dead_code_scan(config: Dict[str, Any]) -> Dict[str, Any]:
    """执行死代码扫描
    
    Args:
        config: 任务配置
    
    Returns:
        扫描结果摘要
    """
    target_dirs = config.get("target_dirs", [])
    output_dir = os.path.expanduser(config.get("output_dir", "~/projects/TwinForge/docs/nightly_reports/"))
    min_confidence = config.get("min_confidence", 80)
    
    results = []
    for project_dir in target_dirs:
        project_dir = os.path.expanduser(project_dir)
        if not os.path.isdir(project_dir):
            continue
        
        project_name = os.path.basename(project_dir)
        dead_items = _scan_with_vulture(project_dir, min_confidence)
        
        if dead_items:
            results.append({
                "project": project_name,
                "dead_count": len(dead_items),
                "items": dead_items[:50],  # 限制报告大小
            })
    
    report = {
        "task": "dead_code_scan",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "projects_scanned": len(target_dirs),
        "total_dead": sum(r["dead_count"] for r in results),
        "details": results,
    }
    
    _write_report(report, output_dir)
    return report


def _scan_with_vulture(project_dir: str, min_confidence: int) -> List[Dict]:
    """使用 vulture 扫描未使用代码"""
    try:
        cmd = ["python", "-m", "vulture", project_dir,
               f"--min-confidence={min_confidence}", "--json"]
        output = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=120)
        
        items = json.loads(output) if output.strip() else []
        return [
            {
                "file": item.get("filename", ""),
                "line": item.get("lineno", 0),
                "name": item.get("name", ""),
                "type": item.get("type", "unknown"),
                "confidence": item.get("confidence", 0),
            }
            for item in items
        ]
    except FileNotFoundError:
        # vulture 未安装，使用简单 fallback
        return _fallback_scan(project_dir)
    except Exception as e:
        print(f"[dead_code] vulture error: {e}")
        return []


def _fallback_scan(project_dir: str) -> List[Dict]:
    """简单 fallback：扫描 TODO/FIXME 标记"""
    items = []
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", ".venv", "node_modules")]
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    for i, line in enumerate(f, 1):
                        if "def _" in line and "unused" in line.lower():
                            items.append({
                                "file": os.path.relpath(fpath, project_dir),
                                "line": i,
                                "name": line.strip(),
                                "type": "function",
                                "confidence": 60,
                            })
            except Exception:
                pass
    return items


def _write_report(report: Dict, output_dir: str):
    """写入报告"""
    os.makedirs(output_dir, exist_ok=True)
    date_str = report["date"]
    
    json_path = os.path.join(output_dir, f"{date_str}_dead_code_scan.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    md_path = os.path.join(output_dir, f"{date_str}_dead_code_scan.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# 死代码检测报告 — {date_str}\n\n")
        f.write(f"- 扫描项目: {report['projects_scanned']} 个\n")
        f.write(f"- 发现未使用代码: {report['total_dead']} 项\n\n")
        for d in report["details"]:
            f.write(f"## {d['project']} ({d['dead_count']} 项)\n\n")
            f.write(f"| 文件 | 行号 | 名称 | 类型 | 置信度 |\n")
            f.write(f"|------|------|------|------|--------|\n")
            for item in d["items"][:20]:
                f.write(f"| {item['file']} | {item['line']} | {item['name']} | {item['type']} | {item['confidence']}% |\n")
            if d["dead_count"] > 20:
                f.write(f"\n... 还有 {d['dead_count'] - 20} 项，详见 JSON 报告\n")
    
    print(f"[dead_code] 报告: {md_path}")
