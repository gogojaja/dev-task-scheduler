"""
夜间代码任务

定义三个可调度的夜间任务：
- nightly_code_review: 代码走查（02:00）
- nightly_code_completion: 代码补全建议（03:00）
- nightly_test_generation: 单元测试生成（04:00）
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime
from typing import List, Dict, Any

# 确保 dev-model-router 在 Python 路径中
ROUTER_PATH = os.environ.get("DEV_MODEL_ROUTER_PATH", "")
if ROUTER_PATH and ROUTER_PATH not in sys.path:
    sys.path.insert(0, ROUTER_PATH)

from ..registry import register_task
from ..models import TaskResult, TaskContext
from .target_scanner import TargetScanner
from .report_writer import NightlyReportWriter

logger = logging.getLogger(__name__)


def _get_config(context: TaskContext) -> Dict[str, Any]:
    """从 context.params 获取夜间任务配置"""
    return {
        "target_dirs": context.params.get("target_dirs", []),
        "exclude_patterns": context.params.get("exclude_patterns", []),
        "output_dir": context.params.get("output_dir", "~/nightly_reports/"),
        "model": context.params.get("model", "qwen2.5-coder:14b"),
        "ollama_host": context.params.get("ollama_host", "127.0.0.1"),
        "ollama_port": context.params.get("ollama_port", 11500),
    }


def _get_executor(config: Dict[str, Any]):
    """创建 CodeTaskExecutor 实例"""
    try:
        from executor.ollama_client import OllamaClient
        from executor.code_executor import CodeTaskExecutor
    except ImportError as e:
        raise ImportError(
            f"dev-model-router 未安装或不在 PYTHONPATH 中: {e}"
        ) from e

    client = OllamaClient(
        host=config["ollama_host"],
        port=config["ollama_port"],
    )
    return CodeTaskExecutor(client=client, model=config["model"])


@register_task(
    name="nightly_code_review",
    trigger="cron",
    hour=2,
    minute=0,
    description="夜间代码走查：扫描目标目录，逐文件调用 14B 走查",
    idempotency_key="{date}",
    timeout=1800,
    max_retries=1,
)
def nightly_code_review(context: TaskContext):
    """夜间代码走查"""
    config = _get_config(context)
    start_time = time.time()

    # 检查 Ollama 可用性
    try:
        from executor.ollama_client import OllamaClient
        client = OllamaClient(host=config["ollama_host"], port=config["ollama_port"])
        if not client.is_available():
            return TaskResult.fail(
                message="Ollama 服务不可用",
                error_code="SCH-NIGHT-001",
                skip_retry=True,
            )
    except Exception as e:
        return TaskResult.fail(message=f"Ollama 连接失败: {e}", error_code="SCH-NIGHT-002")

    # 扫描目标文件
    scanner = TargetScanner(
        target_dirs=config["target_dirs"],
        exclude_patterns=config["exclude_patterns"],
    )
    files = scanner.scan()
    if not files:
        return TaskResult.ok(message="无目标文件", data={"files_count": 0})

    # 逐文件走查
    executor = _get_executor(config)
    results: List[Dict] = []
    total_issues = 0

    for fi in files:
        try:
            with open(fi.path, "r", encoding="utf-8") as f:
                source = f.read()
            review = executor.review_code(source, fi.path)
            result = {
                "file_path": fi.path,
                "issues": [
                    {
                        "line": i.line,
                        "severity": i.severity,
                        "category": i.category,
                        "description": i.description,
                        "suggestion": i.suggestion,
                    }
                    for i in review.issues
                ],
                "tokens": review.tokens,
                "duration_s": review.duration_s,
                "error": review.error,
            }
            total_issues += len(review.issues)
        except Exception as e:
            result = {"file_path": fi.path, "issues": [], "error": str(e)}
        results.append(result)

    duration = time.time() - start_time

    # 生成报告
    writer = NightlyReportWriter(output_dir=config["output_dir"])
    report_path = writer.write(
        task_name="code_review",
        results=results,
        metadata={"files_count": len(files), "duration_s": duration, "total_issues": total_issues},
    )

    return TaskResult.ok(
        message=f"代码走查完成: {len(files)} 文件, {total_issues} 问题, 耗时 {duration:.0f}s",
        data={
            "files_reviewed": len(files),
            "issues_found": total_issues,
            "report_path": report_path,
            "duration_s": duration,
        },
    )


@register_task(
    name="nightly_code_completion",
    trigger="cron",
    hour=3,
    minute=0,
    description="夜间代码补全建议",
    idempotency_key="{date}",
    timeout=1800,
    max_retries=1,
)
def nightly_code_completion(context: TaskContext):
    """夜间代码补全建议"""
    config = _get_config(context)
    start_time = time.time()

    try:
        from executor.ollama_client import OllamaClient
        client = OllamaClient(host=config["ollama_host"], port=config["ollama_port"])
        if not client.is_available():
            return TaskResult.fail(message="Ollama 服务不可用", error_code="SCH-NIGHT-001", skip_retry=True)
    except Exception as e:
        return TaskResult.fail(message=f"Ollama 连接失败: {e}", error_code="SCH-NIGHT-002")

    scanner = TargetScanner(
        target_dirs=config["target_dirs"],
        exclude_patterns=config["exclude_patterns"],
    )
    files = scanner.scan()
    if not files:
        return TaskResult.ok(message="无目标文件", data={"files_count": 0})

    executor = _get_executor(config)
    results: List[Dict] = []

    for fi in files:
        try:
            with open(fi.path, "r", encoding="utf-8") as f:
                source = f.read()
            # 在文件末尾添加 FILL_HERE 标记，让模型建议后续代码
            source_with_marker = source + "\n# <FILL_HERE>\n"
            completion = executor.suggest_completion(source_with_marker, fi.path)
            result = {
                "file_path": fi.path,
                "completion_code": completion.completion_code[:500],  # 截断
                "explanation": completion.explanation[:200],
                "tokens": completion.tokens,
                "duration_s": completion.duration_s,
                "error": completion.error,
            }
        except Exception as e:
            result = {"file_path": fi.path, "completion_code": "", "error": str(e)}
        results.append(result)

    duration = time.time() - start_time
    writer = NightlyReportWriter(output_dir=config["output_dir"])
    report_path = writer.write(
        task_name="code_completion",
        results=results,
        metadata={"files_count": len(files), "duration_s": duration},
    )

    return TaskResult.ok(
        message=f"代码补全完成: {len(files)} 文件, 耗时 {duration:.0f}s",
        data={"files_processed": len(files), "report_path": report_path, "duration_s": duration},
    )


@register_task(
    name="nightly_test_generation",
    trigger="cron",
    hour=4,
    minute=0,
    description="夜间单元测试生成",
    idempotency_key="{date}",
    timeout=1800,
    max_retries=1,
)
def nightly_test_generation(context: TaskContext):
    """夜间单元测试生成"""
    config = _get_config(context)
    start_time = time.time()

    try:
        from executor.ollama_client import OllamaClient
        client = OllamaClient(host=config["ollama_host"], port=config["ollama_port"])
        if not client.is_available():
            return TaskResult.fail(message="Ollama 服务不可用", error_code="SCH-NIGHT-001", skip_retry=True)
    except Exception as e:
        return TaskResult.fail(message=f"Ollama 连接失败: {e}", error_code="SCH-NIGHT-002")

    scanner = TargetScanner(
        target_dirs=config["target_dirs"],
        exclude_patterns=config.get("exclude_patterns", []) + ["test_*.py"],
    )
    files = scanner.scan()
    if not files:
        return TaskResult.ok(message="无目标文件", data={"files_count": 0})

    executor = _get_executor(config)
    results: List[Dict] = []

    for fi in files:
        try:
            with open(fi.path, "r", encoding="utf-8") as f:
                source = f.read()
            test_gen = executor.generate_tests(source, fi.path)
            result = {
                "file_path": fi.path,
                "test_code": test_gen.test_code,
                "tokens": test_gen.tokens,
                "duration_s": test_gen.duration_s,
                "error": test_gen.error,
            }
        except Exception as e:
            result = {"file_path": fi.path, "test_code": "", "error": str(e)}
        results.append(result)

    duration = time.time() - start_time
    writer = NightlyReportWriter(output_dir=config["output_dir"])
    report_path = writer.write(
        task_name="test_generation",
        results=results,
        metadata={"files_count": len(files), "duration_s": duration},
    )

    return TaskResult.ok(
        message=f"测试生成完成: {len(files)} 文件, 耗时 {duration:.0f}s",
        data={"files_processed": len(files), "report_path": report_path, "duration_s": duration},
    )
