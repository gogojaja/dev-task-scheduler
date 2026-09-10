"""Commit Message 质量审查任务（v2 — 接入 RoutedExecutor 路由层）

提取近 24h 的 commit messages，通过 RoutedExecutor 按复杂度路由到 14B 或云端模型审查。
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

    # 创建路由执行器
    executor = _get_routed_executor(config)
    
    results = []
    for project_dir in target_dirs:
        project_dir = os.path.expanduser(project_dir)
        if not os.path.isdir(project_dir):
            continue
        
        project_name = os.path.basename(project_dir)
        commits = _get_recent_commits(project_dir)
        if not commits:
            continue
        
        # 通过路由执行器审查（自动选择本地/云端）
        review_result = _review_with_llm(commits, executor)
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


def _get_routed_executor(config: Dict[str, Any]):
    """创建路由执行器（复用 nightly_code_tasks 的工厂逻辑）。"""
    try:
        from .nightly_code_tasks import _get_routed_executor as _factory
        return _factory(config)
    except ImportError:
        # 回退：直接创建 OllamaClient 包装
        from executor.ollama_client import OllamaClient
        client = OllamaClient(host=config["ollama_host"], port=config["ollama_port"])
        return client, config.get("model", "qwen2.5-coder:14b")


def _review_with_llm(commits, executor) -> List[Dict]:
    """通过路由执行器审查 commit messages"""
    try:
        from executor.prompts.commit_review import SYSTEM_PROMPT, USER_TEMPLATE

        commits_text = "\n".join(commits)
        prompt = USER_TEMPLATE.format(commit_messages=commits_text)

        # 判断 executor 类型：RoutedExecutor 或 OllamaClient 回退
        try:
            from executor.routed_executor import RoutedExecutor
            if isinstance(executor, RoutedExecutor):
                resp = executor.run_prompt(
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=prompt,
                    task_type="commit_review",
                    context_hint=f"commit review: {commits_text[:300]}",
                )
                content = resp["text"]
            else:
                raise TypeError("not RoutedExecutor")
        except (ImportError, TypeError):
            # 回退：直接调用 OllamaClient
            client, model = executor
            ollama_resp = client.generate(
                model=model, prompt=prompt,
                system=SYSTEM_PROMPT, task_type="commit_review",
            )
            content = ollama_resp.text

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
