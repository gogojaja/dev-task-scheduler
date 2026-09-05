"""文档同步检查任务（v1 — 检测代码变更后文档是否同步更新）

扫描 git diff，对比代码变更与文档修改时间，生成「文档过期清单」。
"""
import json
import os
import re
import subprocess
from datetime import datetime, timedelta
from typing import Any, Dict, List


def run_doc_sync(config: Dict[str, Any]) -> Dict[str, Any]:
    """执行文档同步检查
    
    Args:
        config: 任务配置，包含 target_dirs, output_dir 等
    
    Returns:
        检查结果摘要
    """
    target_dirs = config.get("target_dirs", [])
    output_dir = os.path.expanduser(config.get("output_dir", "~/projects/TwinForge/docs/nightly_reports/"))
    
    # 获取上次执行时间
    last_run = _get_last_run_time(config)
    since_str = last_run.strftime("%Y-%m-%d") if last_run else "7.days.ago"
    
    results = []
    for project_dir in target_dirs:
        project_dir = os.path.expanduser(project_dir)
        if not os.path.isdir(project_dir):
            continue
        
        # 获取代码变更文件
        changed_code = _get_changed_files(project_dir, since_str, code_only=True)
        if not changed_code:
            continue
        
        # 获取文档变更文件
        changed_docs = _get_changed_files(project_dir, since_str, docs_only=True)
        
        # 对比：哪些代码变了但文档没跟着变
        stale_docs = _find_stale_docs(project_dir, changed_code, changed_docs)
        results.extend(stale_docs)
    
    # 写报告
    report = {
        "task": "doc_sync",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "since": since_str,
        "stale_count": len(results),
        "items": results,
    }
    
    _write_report(report, output_dir)
    return report


def _get_last_run_time(config: Dict) -> datetime:
    """从 execution_log.csv 读取上次执行时间"""
    csv_path = os.path.expanduser(
        config.get("csv_path", "~/projects/TwinForge/docs/nightly_reports/execution_log.csv")
    )
    try:
        if os.path.exists(csv_path):
            with open(csv_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            for line in reversed(lines):
                if "doc_sync" in line:
                    parts = line.strip().split(",")
                    if len(parts) >= 3:
                        return datetime.strptime(parts[-1].strip(), "%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    return datetime.now() - timedelta(days=7)


def _get_changed_files(project_dir: str, since: str, code_only: bool = False, docs_only: bool = False) -> List[str]:
    """获取 git 变更文件列表"""
    try:
        cmd = ["git", "-C", project_dir, "log", f"--since={since}", "--name-only", "--format="]
        output = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=30)
        files = [f.strip() for f in output.strip().split("\n") if f.strip()]
        
        if code_only:
            files = [f for f in files if f.endswith((".py", ".sh", ".yaml", ".yml", ".json"))]
        elif docs_only:
            files = [f for f in files if f.endswith((".md", ".rst", ".txt"))]
        
        return files
    except Exception:
        return []


def _find_stale_docs(project_dir: str, changed_code: List[str], changed_docs: List[str]) -> List[Dict]:
    """找出代码变了但文档没跟着变的项"""
    stale = []
    doc_dirs = {"docs", "doc", "references", "README"}
    
    for code_file in changed_code:
        # 提取模块名（从文件名推断可能关联的文档）
        module_name = os.path.splitext(os.path.basename(code_file))[0]
        
        # 检查是否有对应文档被更新
        doc_updated = any(module_name in doc_file for doc_file in changed_docs)
        if not doc_updated:
            # 检查是否存在相关文档
            related_doc = _find_related_doc(project_dir, module_name)
            if related_doc:
                stale.append({
                    "code_file": code_file,
                    "related_doc": related_doc,
                    "reason": f"代码 {code_file} 已变更，但关联文档 {related_doc} 未更新",
                })
    
    return stale


def _find_related_doc(project_dir: str, module_name: str) -> str:
    """查找与模块名关联的文档"""
    docs_dir = os.path.join(project_dir, "docs")
    if not os.path.isdir(docs_dir):
        return ""
    
    for root, dirs, files in os.walk(docs_dir):
        for f in files:
            if f.endswith(".md") and module_name.lower() in f.lower():
                return os.path.relpath(os.path.join(root, f), project_dir)
    return ""


def _write_report(report: Dict, output_dir: str):
    """写入报告"""
    os.makedirs(output_dir, exist_ok=True)
    date_str = report["date"]
    
    # JSON 报告
    json_path = os.path.join(output_dir, f"{date_str}_doc_sync.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    # Markdown 报告
    md_path = os.path.join(output_dir, f"{date_str}_doc_sync.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# 文档同步检查报告 — {date_str}\n\n")
        f.write(f"> 检查范围: {report['since']} 以来的变更\n\n")
        f.write(f"## 摘要\n\n")
        f.write(f"- 发现 {report['stale_count']} 个文档需要同步更新\n\n")
        if report["items"]:
            f.write(f"## 过期文档清单\n\n")
            f.write(f"| 代码文件 | 关联文档 | 原因 |\n")
            f.write(f"|----------|----------|------|\n")
            for item in report["items"]:
                f.write(f"| {item['code_file']} | {item['related_doc']} | {item['reason']} |\n")
    
    print(f"[doc_sync] 报告: {md_path}")
