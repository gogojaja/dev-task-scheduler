"""技术债务分类任务（v2 — 接入 RoutedExecutor 路由层）

扫描代码中的 TODO 标记，通过 RoutedExecutor 按复杂度路由到 14B 或云端模型进行分类。
"""
from ..registry import register_task

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List


@register_task(
    name="nightly_debt_classify",
    trigger="cron", day_of_week="mon", hour=4, minute=30,
    description="nightly_debt_classify",
)
def run_debt_classify(config: Dict[str, Any]) -> Dict[str, Any]:
    """执行技术债务分类
    
    Args:
        config: 任务配置
    
    Returns:
        分类结果摘要
    """
    target_dirs = config.get("target_dirs", [])
    output_dir = os.path.expanduser(config.get("output_dir", "~/projects/TwinForge/docs/nightly_reports/"))

    # 创建路由执行器
    executor = _get_routed_executor(config)
    
    # 扫描所有 TODO 标记
    all_todos = []
    for project_dir in target_dirs:
        project_dir = os.path.expanduser(project_dir)
        if not os.path.isdir(project_dir):
            continue
        project_name = os.path.basename(project_dir)
        todos = _scan_todo_markers(project_dir)
        for t in todos:
            t["project"] = project_name
        all_todos.extend(todos)
    
    if not all_todos:
        report = {"task": "debt_classify", "date": datetime.now().strftime("%Y-%m-%d"),
                  "total_todos": 0, "classified": []}
        _write_report(report, output_dir)
        return report
    
    # 调用 14B 分类（分批，每批最多 50 项）
    classified = []
    batch_size = 50
    for i in range(0, len(all_todos), batch_size):
        batch = all_todos[i:i+batch_size]
        result = _classify_with_llm(batch, executor)
        classified.extend(result)
    
    report = {
        "task": "debt_classify",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "total_todos": len(all_todos),
        "classified_count": len(classified),
        "by_type": _count_by(classified, "debt_type"),
        "by_priority": _count_by(classified, "priority"),
        "classified": classified,
    }
    
    _write_report(report, output_dir)
    return report


def _scan_todo_markers(project_dir: str) -> List[Dict]:
    """扫描 TODO/FIXME/HACK 标记"""
    markers = []
    pattern = re.compile(r'#\s*(TODO|FIXME|HACK|XXX)\s*:?\s*(.*)', re.IGNORECASE)
    
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", ".venv", "node_modules")]
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    for line_num, line in enumerate(f, 1):
                        match = pattern.search(line)
                        if match:
                            markers.append({
                                "file": os.path.relpath(fpath, project_dir),
                                "line": line_num,
                                "marker": match.group(1).upper(),
                                "comment": match.group(2).strip(),
                            })
            except Exception:
                pass
    
    return markers


def _get_routed_executor(config: Dict[str, Any]):
    """创建路由执行器（复用 nightly_code_tasks 的工厂逻辑）。"""
    try:
        from .nightly_code_tasks import _get_routed_executor as _factory
        return _factory(config)
    except ImportError:
        from executor.ollama_client import OllamaClient
        client = OllamaClient(host=config["ollama_host"], port=config["ollama_port"])
        return client, config.get("model", "qwen2.5-coder:14b")


def _classify_with_llm(todos: List[Dict], executor) -> List[Dict]:
    """通过路由执行器分类"""
    try:
        from executor.prompts.debt_classify import SYSTEM_PROMPT, USER_TEMPLATE

        todos_text = "\n".join(
            f"{t['file']}:{t['line']} [{t['marker']}] {t['comment']}"
            for t in todos
        )
        prompt = USER_TEMPLATE.format(todo_items=todos_text)

        # 判断 executor 类型
        try:
            from executor.routed_executor import RoutedExecutor
            if isinstance(executor, RoutedExecutor):
                resp = executor.run_prompt(
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=prompt,
                    task_type="debt_classify",
                    context_hint=f"debt classify: {todos_text[:300]}",
                )
                content = resp["text"]
            else:
                raise TypeError("not RoutedExecutor")
        except (ImportError, TypeError):
            client, model = executor
            ollama_resp = client.generate(
                model=model, prompt=prompt,
                system=SYSTEM_PROMPT, task_type="debt_classify",
            )
            content = ollama_resp.text

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return [{"raw_response": content[:500]}]
    except Exception as e:
        return [{"error": str(e)}]


def _count_by(items: List[Dict], key: str) -> Dict[str, int]:
    """按字段统计"""
    counts = {}
    for item in items:
        val = item.get(key, "unknown")
        counts[val] = counts.get(val, 0) + 1
    return counts


def _write_report(report: Dict, output_dir: str):
    """写入报告"""
    os.makedirs(output_dir, exist_ok=True)
    date_str = report["date"]
    
    json_path = os.path.join(output_dir, f"{date_str}_debt_classify.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    md_path = os.path.join(output_dir, f"{date_str}_debt_classify.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# 技术债务分类报告 — {date_str}\n\n")
        f.write(f"- 扫描 TODO 标记: {report['total_todos']} 个\n")
        f.write(f"- 已分类: {report.get('classified_count', 0)} 个\n\n")
        
        if report.get("by_type"):
            f.write("## 按类型分布\n\n")
            for t, c in report["by_type"].items():
                f.write(f"- {t}: {c} 项\n")
            f.write("\n")
        
        if report.get("by_priority"):
            f.write("## 按优先级分布\n\n")
            for p, c in sorted(report["by_priority"].items()):
                f.write(f"- {p}: {c} 项\n")
            f.write("\n")
        
        if report.get("classified"):
            f.write("## 详细清单\n\n")
            f.write("| 文件 | 行号 | 标记 | 类型 | 优先级 | 工作量 |\n")
            f.write("|------|------|------|------|--------|--------|\n")
            for item in report["classified"][:30]:
                f.write(f"| {item.get('file','')} | {item.get('line','')} | "
                        f"{item.get('marker','')} | {item.get('debt_type','')} | "
                        f"{item.get('priority','')} | {item.get('effort','')} |\n")
    
    print(f"[debt_classify] 报告: {md_path}")
