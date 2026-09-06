"""Commit Message 质量审查任务（v1 — 14B LLM 审查）

提取近 24h 的 commit messages，调用 14B 模型审查 Conventional Commits 合规性。
"""
from ..registry import register_task

import json
import os
import subprocess
from datetime import datetime
from typing import Any, Dict, List


@register_task(
    name="nightly_commit_review",
    trigger="cron", hour=4, minute=15,
    description="nightly_commit_review",
)
def run_commit_review(config: Dict[str, Any]) -> Dict[str, Any]:
    """执行 commit message 审查
    
    Args:
        config: 任务配置，包含 target_dirs, model, ollama_host 等
    
    Returns:
        审查结果摘要
    """
    target_dirs = config.get("target_dirs", [])
    output_dir = os.path.expanduser(config.get("output_dir", "~/projects/TwinForge/docs/nightly_reports/"))
    model = config.get("model", "qwen2.5-coder:14b")
    ollama_host = config.get("ollama_host", "127.0.0.1")
    ollama_port = config.get("ollama_port", 11434)
    
    results = []
    for project_dir in target_dirs:
        project_dir = os.path.expanduser(project_dir)
        if not os.path.isdir(project_dir):
            continue
        
        project_name = os.path.basename(project_dir)
        commits = _get_recent_commits(project_dir)
        if not commits:
            continue
        
        # 调用 14B 审查
        review_result = _review_with_llm(commits, model, ollama_host, ollama_port)
        results.append({
            "project": project_name,
            "commit_count": len(commits),
            "review": review_result,
        })
    
    report = {
        "task": "commit_review",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "projects_reviewed": len(results),
        "details": results,
    }
    
    _write_report(report, output_dir)
    return report


def _get_recent_commits(project_dir: str) -> List[str]:
    """获取最近 24h 的 commit messages"""
    try:
        cmd = ["git", "-C", project_dir, "log", "--since=24.hours.ago",
               "--format=%h %s", "--no-merges"]
        output = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=30)
        return [line.strip() for line in output.strip().split("\n") if line.strip()]
    except Exception:
        return []


def _review_with_llm(commits: List[str], model: str, host: str, port: int) -> List[Dict]:
    """调用 14B 模型审查 commit messages"""
    try:
        from executor.prompts.commit_review import SYSTEM_PROMPT, USER_TEMPLATE
        
        commits_text = "\n".join(commits)
        prompt = USER_TEMPLATE.format(commit_messages=commits_text)
        
        import urllib.request
        payload = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        }).encode("utf-8")
        
        req = urllib.request.Request(
            f"http://{host}:{port}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=120)
        result = json.loads(resp.read().decode("utf-8"))
        
        content = result.get("message", {}).get("content", "[]")
        # 尝试解析 JSON
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return [{"raw_response": content[:500]}]
    except Exception as e:
        return [{"error": str(e)}]


def _write_report(report: Dict, output_dir: str):
    """写入报告"""
    os.makedirs(output_dir, exist_ok=True)
    date_str = report["date"]
    
    json_path = os.path.join(output_dir, f"{date_str}_commit_review.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    md_path = os.path.join(output_dir, f"{date_str}_commit_review.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Commit Message 审查报告 — {date_str}\n\n")
        f.write(f"- 审查项目: {report['projects_reviewed']} 个\n\n")
        for d in report["details"]:
            f.write(f"## {d['project']} ({d['commit_count']} commits)\n\n")
            for item in d["review"]:
                compliant = item.get("compliant", "N/A")
                status = "PASS" if compliant else "FAIL"
                msg = item.get("message", item.get("raw_response", str(item))[:80])
                f.write(f"- [{status}] {msg}\n")
                if item.get("issues"):
                    for issue in item["issues"]:
                        f.write(f"  - {issue}\n")
            f.write("\n")
    
    print(f"[commit_review] 报告: {md_path}")
