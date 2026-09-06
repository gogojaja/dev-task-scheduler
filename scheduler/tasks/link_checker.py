"""文档链接检查任务（v1 — 检查 Markdown 文档中的链接有效性）

正则提取 Markdown 链接 + HTTP HEAD 验证。
"""
from ..registry import register_task

import json
import os
import re
import urllib.request
import urllib.error
from datetime import datetime
from typing import Any, Dict, List
from concurrent.futures import ThreadPoolExecutor, as_completed


@register_task(
    name="nightly_link_check",
    trigger="cron", day_of_week="fri", hour=4, minute=0,
    description="nightly_link_check",
)
def run_link_check(config: Dict[str, Any]) -> Dict[str, Any]:
    """执行链接检查
    
    Args:
        config: 任务配置
    
    Returns:
        检查结果摘要
    """
    target_dirs = config.get("target_dirs", [])
    output_dir = os.path.expanduser(config.get("output_dir", "~/projects/TwinForge/docs/nightly_reports/"))
    timeout = config.get("link_timeout", 10)
    
    results = []
    for project_dir in target_dirs:
        project_dir = os.path.expanduser(project_dir)
        if not os.path.isdir(project_dir):
            continue
        
        project_name = os.path.basename(project_dir)
        links = _extract_links(project_dir)
        
        if links:
            broken = _check_links(links, timeout)
            results.append({
                "project": project_name,
                "total_links": len(links),
                "broken_count": len(broken),
                "broken": broken,
            })
    
    report = {
        "task": "link_check",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "total_links": sum(r["total_links"] for r in results),
        "total_broken": sum(r["broken_count"] for r in results),
        "details": results,
    }
    
    _write_report(report, output_dir)
    return report


def _extract_links(project_dir: str) -> List[Dict]:
    """从 Markdown 文件中提取链接"""
    links = []
    # Markdown 链接模式: [text](url) 或 <url>
    md_pattern = re.compile(r'\[([^\]]*)\]\(([^)]+)\)')
    angle_pattern = re.compile(r'<(https?://[^>]+)>')
    
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", ".venv", "node_modules")]
        for fname in files:
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    for line_num, line in enumerate(f, 1):
                        # [text](url) 形式
                        for match in md_pattern.finditer(line):
                            url = match.group(2)
                            if url.startswith(("http://", "https://")):
                                links.append({
                                    "file": os.path.relpath(fpath, project_dir),
                                    "line": line_num,
                                    "url": url,
                                    "text": match.group(1),
                                })
                        # <url> 形式
                        for match in angle_pattern.finditer(line):
                            links.append({
                                "file": os.path.relpath(fpath, project_dir),
                                "line": line_num,
                                "url": match.group(1),
                                "text": "",
                            })
            except Exception:
                pass
    
    return links


def _check_links(links: List[Dict], timeout: int) -> List[Dict]:
    """验证链接有效性（HTTP HEAD）"""
    broken = []
    
    def check_one(link: Dict) -> Dict:
        url = link["url"]
        try:
            req = urllib.request.Request(url, method="HEAD",
                                        headers={"User-Agent": "TwinForge-LinkChecker/1.0"})
            resp = urllib.request.urlopen(req, timeout=timeout)
            return {"status": resp.status, "ok": True}
        except urllib.error.HTTPError as e:
            return {"status": e.code, "ok": False}
        except Exception as e:
            # 重试 GET（某些服务器不支持 HEAD）
            try:
                req = urllib.request.Request(url,
                                            headers={"User-Agent": "TwinForge-LinkChecker/1.0"})
                resp = urllib.request.urlopen(req, timeout=timeout)
                return {"status": resp.status, "ok": True}
            except Exception:
                return {"status": 0, "ok": False}
    
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(check_one, link): link for link in links}
        for future in as_completed(futures):
            link = futures[future]
            result = future.result()
            if not result["ok"]:
                broken.append({
                    "file": link["file"],
                    "line": link["line"],
                    "url": link["url"],
                    "status": result["status"],
                })
    
    return broken


def _write_report(report: Dict, output_dir: str):
    """写入报告"""
    os.makedirs(output_dir, exist_ok=True)
    date_str = report["date"]
    
    json_path = os.path.join(output_dir, f"{date_str}_link_check.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    md_path = os.path.join(output_dir, f"{date_str}_link_check.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# 文档链接检查报告 — {date_str}\n\n")
        f.write(f"- 检查链接: {report['total_links']} 个\n")
        f.write(f"- 失效链接: {report['total_broken']} 个\n\n")
        if report["details"]:
            for d in report["details"]:
                if d["broken"]:
                    f.write(f"## {d['project']} ({d['broken_count']}/{d['total_links']} 失效)\n\n")
                    f.write(f"| 文件 | 行号 | URL | 状态码 |\n")
                    f.write(f"|------|------|-----|--------|\n")
                    for b in d["broken"]:
                        f.write(f"| {b['file']} | {b['line']} | {b['url']} | {b['status']} |\n")
                    f.write("\n")
    
    print(f"[link_check] 报告: {md_path}")
