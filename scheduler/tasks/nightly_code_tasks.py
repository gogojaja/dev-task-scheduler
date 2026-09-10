"""
夜间代码质量任务（v2 — 增量扫描 + 安全扫描 + 测试执行 + 缺陷登记）

三个可调度的代码任务：
- code_review: 代码走查 + 安全扫描 + 缺陷/RAID 登记（10:00）
- code_completion: TODO/FIXME 感知补全（11:00）
- test_generation: 测试生成 + 执行验证 + 覆盖率（14:00）
- quality_report: 质量趋势汇总（04:45）
"""

from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

# ── 外部项目路径（从环境变量注入）──
for env_key in ("DEV_MODEL_ROUTER_PATH", "DEV_SECURITY_TOOLS_PATH",
                "DEV_TEST_TOOLS_PATH", "DEV_PROJECT_MGMT_PATH"):
    _p = os.environ.get(env_key, "")
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

from ..registry import register_task
from ..models import TaskResult, TaskContext
from .target_scanner import TargetScanner
from .report_writer import NightlyReportWriter
from .circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)


# ── 环境预检 + 熔断器（四层保护集成） ──

def _get_circuit_breaker(config: Dict[str, Any]) -> CircuitBreaker:
    """从配置获取熔断器实例"""
    cb_cfg = config.get("circuit_breaker", {})
    csv_path = cb_cfg.get("csv_path", "~/projects/TwinForge/docs/nightly_reports/circuit_breaker.csv")
    threshold = cb_cfg.get("failure_threshold", 3)
    return CircuitBreaker(csv_path=csv_path, failure_threshold=threshold)


def _check_circuit(task_name: str, config: Dict[str, Any]) -> Optional[TaskResult]:
    """检查熔断器，如果已熔断返回 fail TaskResult，否则返回 None"""
    if not config.get("circuit_breaker", {}).get("enabled", True):
        return None
    try:
        cb = _get_circuit_breaker(config)
        if not cb.should_execute(task_name):
            states = cb.get_all_states()
            detail = ""
            for s in states:
                if s.task_name == task_name:
                    detail = s.detail
                    break
            logger.warning("[circuit_breaker] %s is OPEN, skipping: %s", task_name, detail)
            return TaskResult.fail(
                message=f"任务 {task_name} 已熔断（连续失败 >= 阈值）: {detail}",
                error_code="CB-OPEN",
                skip_retry=True,
            )
    except Exception as e:
        logger.warning("[circuit_breaker] check failed: %s", e)
    return None


def _record_task_result(task_name: str, success: bool, config: Dict[str, Any], detail: str = ""):
    """记录任务执行结果到熔断器"""
    if not config.get("circuit_breaker", {}).get("enabled", True):
        return
    try:
        cb = _get_circuit_breaker(config)
        if success:
            cb.record_success(task_name)
        else:
            cb.record_failure(task_name, detail)
    except Exception as e:
        logger.warning("[circuit_breaker] record failed: %s", e)


# ── 可选模块导入（优雅降级）──

def _try_import_security():
    """尝试导入 dev-security-tools"""
    try:
        from src.core import secret, scan
        return secret, scan
    except ImportError:
        logger.warning("dev-security-tools 不可用，安全扫描跳过")
        return None, None


def _try_import_test_tools():
    """尝试导入 dev-test-tools"""
    try:
        from dev_test_tools.core import runner, defect, coverage
        return runner, defect, coverage
    except ImportError:
        logger.warning("dev-test-tools 不可用，测试执行/缺陷登记跳过")
        return None, None, None


def _try_import_project_mgmt():
    """尝试导入 dev-project-mgmt"""
    try:
        from dev_project_mgmt import raid_manager
        return raid_manager
    except ImportError:
        logger.warning("dev-project-mgmt 不可用，RAID 登记跳过")
        return None


def _try_import_audit():
    """尝试导入审计模块"""
    try:
        from src.core import audit
        return audit
    except ImportError:
        return None


# ── 配置 ──

def _get_config(context: TaskContext) -> Dict[str, Any]:
    return {
        "target_dirs": context.params.get("target_dirs", []),
        "exclude_patterns": context.params.get("exclude_patterns", []),
        "output_dir": context.params.get("output_dir", "~/nightly_reports/"),
        "model": context.params.get("model", "qwen2.5-coder:14b"),
        "ollama_host": context.params.get("ollama_host", "127.0.0.1"),
        "ollama_port": context.params.get("ollama_port", 11434),
        "incremental": context.params.get("incremental", True),
        "execution_log": context.params.get(
            "execution_log",
            "~/projects/TwinForge/docs/nightly_reports/execution_log.csv",
        ),
    }


def _get_executor(config: Dict[str, Any]):
    from executor.ollama_client import OllamaClient
    from executor.code_executor import CodeTaskExecutor
    client = OllamaClient(host=config["ollama_host"], port=config["ollama_port"])
    return CodeTaskExecutor(client=client, model=config["model"])


def _get_routed_executor(config: Dict[str, Any]):
    """创建路由执行器（自动按复杂度选择本地/云端模型）。

    如果 dev-model-router 的路由模块不可用，回退到纯本地执行器。
    """
    from executor.ollama_client import OllamaClient

    ollama = OllamaClient(host=config["ollama_host"], port=config["ollama_port"])

    # 尝试加载路由层（优雅降级）
    try:
        from executor.routed_executor import RoutedExecutor
        from executor.cloud_client import CloudClient
        from pathlib import Path

        cloud = CloudClient()  # 从环境变量读取 CLOUD_API_*
        cost_path = config.get("cost_storage", "~/nightly_reports/router_costs.json")

        return RoutedExecutor(
            ollama_client=ollama,
            cloud_client=cloud if cloud.is_configured else None,
            local_model=config["model"],
            cost_storage_path=Path(os.path.expanduser(cost_path)),
            daily_budget=float(os.environ.get("DAILY_BUDGET_LIMIT", "5.0")),
            router_mode=os.environ.get("ROUTER_MODE", "keyword"),
            prefer_local=os.environ.get("ROUTER_PREFER_LOCAL", "true").lower() in ("true", "1", "yes"),
        )
    except ImportError:
        logger.warning("dev-model-router 路由模块不可用，回退到纯本地执行器")
        from executor.code_executor import CodeTaskExecutor
        return CodeTaskExecutor(client=ollama, model=config["model"])


def _extract_routing_metadata(executor: Any) -> Dict[str, Any]:
    """从路由执行器提取统计信息（供报告使用）。"""
    try:
        from executor.routed_executor import RoutedExecutor
        if isinstance(executor, RoutedExecutor):
            stats = executor.get_routing_stats()
            return {
                "routed_local": stats.routed_local,
                "routed_cloud": stats.routed_cloud,
                "degraded_to_local": stats.degraded_to_local,
                "total_cost": stats.total_cost,
                "cloud_ratio": stats.cloud_ratio,
                "routing_enabled": True,
            }
    except (ImportError, AttributeError):
        pass
    return {"routing_enabled": False}


def _connect_ollama(config: Dict[str, Any], task_name: Optional[str] = None) -> Optional[TaskResult]:
    """连接并检查 Ollama 可用性。

    可用返回 None；不可用或连接异常返回对应的 fail TaskResult。
    task_name 非空时，失败路径同步记录熔断器（保留各任务原有行为差异）。
    """
    try:
        from executor.ollama_client import OllamaClient
        client = OllamaClient(host=config["ollama_host"], port=config["ollama_port"])
        if not client.is_available():
            if task_name:
                _record_task_result(task_name, False, config, "Ollama unavailable")
            return TaskResult.fail(message="Ollama 服务不可用",
                                   error_code="SCH-NIGHT-001", skip_retry=True)
    except Exception as e:
        if task_name:
            _record_task_result(task_name, False, config, str(e)[:100])
        return TaskResult.fail(message=f"Ollama 连接失败: {e}", error_code="SCH-NIGHT-002")
    return None


def _get_last_run_time(execution_log: str) -> Optional[datetime]:
    """从 execution_log.csv 读取上次成功执行时间"""
    log_path = os.path.expanduser(execution_log)
    if not os.path.isfile(log_path):
        return None
    try:
        import csv
        with open(log_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        if not rows:
            return None
        last = rows[-1]
        ts = last.get("end_time", "")
        if ts:
            return datetime.fromisoformat(ts)
    except Exception:
        pass
    return None


# ── TODO/FIXME 扫描 ──

_TODO_PATTERN = re.compile(
    r"^\s*#\s*(TODO|FIXME|HACK|XXX|NOTIMPLEMENTED)\b[:\s]*(.*)",
    re.IGNORECASE | re.MULTILINE,
)


def _find_todo_markers(source: str) -> List[Dict]:
    """扫描源码中的 TODO/FIXME/HACK 标记"""
    markers = []
    for i, line in enumerate(source.splitlines(), 1):
        m = _TODO_PATTERN.match(line)
        if m:
            markers.append({
                "line": i,
                "marker_type": m.group(1).upper(),
                "comment": m.group(2).strip(),
            })
    return markers


# ── 安全扫描集成 ──

def _run_security_scan(source: str, file_path: str) -> Dict[str, Any]:
    """对单个文件执行安全扫描，返回扫描摘要"""
    secret_mod, scan_mod = _try_import_security()
    result = {"secret_findings": [], "desensitize_findings": [], "summary": ""}

    if secret_mod:
        try:
            leak = secret_mod.scan_text(source, file_path)
            result["secret_findings"] = [
                {"line": d.line_number, "severity": d.severity.value,
                 "description": d.description}
                for d in leak.detections
            ]
            if leak.has_critical:
                result["summary"] = f"⛔ 检测到 {len(leak.detections)} 个凭据泄漏"
        except Exception as e:
            logger.warning("安全扫描失败 [%s]: %s", file_path, e)

    if scan_mod:
        try:
            des = scan_mod.scan_text(source)
            result["desensitize_findings"] = [
                {"level": str(r.level), "count": len(r.matches)}
                for r in (des.results if hasattr(des, "results") else [])
            ]
        except Exception as e:
            logger.warning("脱敏扫描失败 [%s]: %s", file_path, e)

    if not result["summary"] and result["secret_findings"]:
        result["summary"] = f"发现 {len(result['secret_findings'])} 个安全问题"
    return result


def _build_security_context(security_result: Dict) -> str:
    """将安全扫描结果格式化为 prompt 上下文"""
    if not security_result.get("secret_findings"):
        return "安全扫描：未发现问题"
    lines = ["安全扫描发现以下问题（请重点关注）："]
    for f in security_result["secret_findings"]:
        lines.append(f"  - 行 {f['line']}: [{f['severity']}] {f['description']}")
    return "\n".join(lines)


# ── 缺陷/RAID 登记 ──

def _register_defect(issue: Dict, file_path: str) -> bool:
    """阻断级 issue 登记为缺陷"""
    _, defect_mod, _ = _try_import_test_tools()
    if not defect_mod:
        return False
    try:
        defect_mod.add_defect(
            title=f"[代码走查] {file_path}: {issue.get('description', '')[:60]}",
            severity="高" if issue.get("severity") == "阻断" else "中",
            priority="高",
            discoverer="code_review",
            description=f"类别: {issue.get('category', '-')}\n"
                        f"建议: {issue.get('suggestion', '')[:200]}",
        )
        return True
    except Exception as e:
        logger.warning("缺陷登记失败: %s", e)
        return False


def _register_raid_if_recurring(category: str, count: int, project_root: str) -> bool:
    """同类问题出现 >=3 次时登记 RAID 风险"""
    if count < 3:
        return False
    raid_mod = _try_import_project_mgmt()
    if not raid_mod:
        return False
    try:
        raid_mod.add_raid(
            project_root=project_root,
            raid_type="risk",
            description=f"代码走查反复出现 [{category}] 问题 {count} 次，需系统性修复",
            priority="中",
        )
        return True
    except Exception as e:
        logger.warning("RAID 登记失败: %s", e)
        return False


# ── 审计留痕 ──

def _write_audit(operation: str, target: str, conclusion: str = "完成"):
    audit_mod = _try_import_audit()
    if not audit_mod:
        return
    try:
        audit_mod.write_audit_record(
            operation_type=operation,
            operation_target=target,
            description="夜间代码质量任务",
            conclusion=conclusion,
        )
    except Exception as e:
        logger.warning("审计记录失败: %s", e)


# ══════════════════════════════════════════════════════════════
# 任务 1: 代码走查（10:00，增量 + 安全扫描 + 缺陷登记）
# ══════════════════════════════════════════════════════════════

@register_task(
    name="code_review",
    trigger="cron",
    hour=2,
    minute=30,
    description="代码走查：增量扫描 + 安全扫描 + 缺陷/RAID 登记",
    idempotency_key="{date}-review",
    timeout=1800,
    max_retries=1,
)
def code_review(context: TaskContext):
    """代码走查（v2）"""
    config = _get_config(context)
    start_time = time.time()

    # 熔断器检查（Layer 3）
    cb_result = _check_circuit("code_review", config)
    if cb_result:
        return cb_result

    # Ollama 连接检查
    ollama_fail = _connect_ollama(config)
    if ollama_fail:
        return ollama_fail

    # 文件扫描（增量或全量）
    scanner = TargetScanner(
        target_dirs=config["target_dirs"],
        exclude_patterns=config["exclude_patterns"],
    )
    all_files = scanner.scan()
    if config.get("incremental"):
        last_run = _get_last_run_time(config["execution_log"])
        if last_run:
            files = scanner.filter_changed(all_files, last_run)
            logger.info("增量模式: %d/%d 文件（上次运行: %s）",
                        len(files), len(all_files), last_run.isoformat())
        else:
            files = all_files
            logger.info("增量模式: 无历史记录，全量扫描 %d 文件", len(files))
    else:
        files = all_files

    if not files:
        return TaskResult.ok(message="无目标文件（增量模式无变更）", data={"files_count": 0})

    executor = _get_routed_executor(config)
    results: List[Dict] = []
    total_issues = 0
    security_alerts = 0
    defects_registered = 0

    for fi in files:
        try:
            with open(fi.path, "r", encoding="utf-8") as f:
                source = f.read()

            # 安全扫描
            sec_result = _run_security_scan(source, fi.path)
            security_context = _build_security_context(sec_result)
            if sec_result["secret_findings"]:
                security_alerts += 1

            # 代码走查（传入安全扫描上下文）
            review = executor.review_code(source, fi.path, extra_context=security_context)

            result = {
                "file_path": fi.path,
                "issues": [
                    {"line": i.line, "severity": i.severity, "category": i.category,
                     "description": i.description, "suggestion": i.suggestion,
                     "repeatable": i.repeatable}
                    for i in review.issues
                ],
                "security_findings": sec_result["secret_findings"],
                "security_summary": sec_result["summary"],
                "tokens": review.tokens,
                "duration_s": review.duration_s,
                "error": review.error,
            }
            total_issues += len(review.issues)

            # 阻断级 issue → 缺陷登记
            for issue in result["issues"]:
                if issue["severity"] == "阻断":
                    if _register_defect(issue, fi.path):
                        defects_registered += 1

        except Exception as e:
            result = {"file_path": fi.path, "issues": [], "error": str(e),
                      "security_findings": []}
        results.append(result)

    # RAID 登记：统计同类问题出现次数
    category_counts: Dict[str, int] = {}
    for r in results:
        for i in r.get("issues", []):
            cat = i.get("category", "unknown")
            category_counts[cat] = category_counts.get(cat, 0) + 1
    for cat, count in category_counts.items():
        _register_raid_if_recurring(cat, count, ".")

    duration = time.time() - start_time

    # 报告
    writer = NightlyReportWriter(output_dir=config["output_dir"])
    routing_meta = _extract_routing_metadata(executor)
    report_path = writer.write(
        task_name="code_review",
        results=results,
        metadata={
            "files_count": len(files), "total_files": len(all_files),
            "duration_s": duration, "total_issues": total_issues,
            "security_alerts": security_alerts,
            "defects_registered": defects_registered,
            "incremental": config.get("incremental", True),
            **routing_meta,
        },
    )

    # 审计
    _write_audit("code_review", f"{len(files)} files, {total_issues} issues")

    # 质量指标记录
    try:
        from .quality_tracker import QualityTracker
        tracker = QualityTracker(output_dir=config["output_dir"])
        tracker.record_daily(
            files_scanned=len(files),
            issues_found=total_issues,
            security_alerts=security_alerts,
            defects_registered=defects_registered,
        )
    except ImportError:
        pass

    routing_info = ""
    if routing_meta.get("routing_enabled"):
        routing_info = (f", 路由: {routing_meta['routed_local']}本地/"
                       f"{routing_meta['routed_cloud']}云端")
    result = TaskResult.ok(
        message=f"代码走查完成: {len(files)} 文件, {total_issues} 问题, "
                f"{security_alerts} 安全告警, {defects_registered} 缺陷已登记, "
                f"耗时 {duration:.0f}s{routing_info}",
        data={
            "files_reviewed": len(files), "issues_found": total_issues,
            "security_alerts": security_alerts,
            "defects_registered": defects_registered,
            "report_path": report_path, "duration_s": duration,
            **routing_meta,
        },
    )
    _record_task_result("code_review", True, config)
    return result


# ══════════════════════════════════════════════════════════════
# 任务 2: 代码补全（11:00，TODO/FIXME 感知）
# ══════════════════════════════════════════════════════════════

@register_task(
    name="code_completion",
    trigger="cron",
    hour=3,
    minute=0,
    description="代码补全：TODO/FIXME 感知模式",
    idempotency_key="{date}-completion",
    timeout=1800,
    max_retries=1,
)
def code_completion(context: TaskContext):
    """代码补全（v2 — TODO 感知）"""
    config = _get_config(context)
    start_time = time.time()

    # 熔断器检查（Layer 3）
    cb_result = _check_circuit("code_completion", config)
    if cb_result:
        return cb_result

    ollama_fail = _connect_ollama(config, "code_completion")
    if ollama_fail:
        return ollama_fail

    scanner = TargetScanner(
        target_dirs=config["target_dirs"],
        exclude_patterns=config["exclude_patterns"],
    )
    all_files = scanner.scan()
    if config.get("incremental"):
        last_run = _get_last_run_time(config["execution_log"])
        files = scanner.filter_changed(all_files, last_run) if last_run else all_files
    else:
        files = all_files

    if not files:
        return TaskResult.ok(message="无目标文件", data={"files_count": 0})

    executor = _get_routed_executor(config)
    results: List[Dict] = []
    total_markers = 0

    for fi in files:
        try:
            with open(fi.path, "r", encoding="utf-8") as f:
                source = f.read()

            # 扫描 TODO/FIXME 标记
            markers = _find_todo_markers(source)
            if not markers:
                continue  # 无标记则跳过

            for marker in markers:
                total_markers += 1
                completion = executor.suggest_completion(
                    source, fi.path,
                    marker_type=marker["marker_type"],
                    marker_line=marker["line"],
                    marker_comment=marker["comment"],
                )
                results.append({
                    "file_path": fi.path,
                    "marker_line": marker["line"],
                    "marker_type": marker["marker_type"],
                    "marker_comment": marker["comment"],
                    "completion_code": completion.completion_code[:500],
                    "confidence": completion.confidence,
                    "test_hints": completion.test_hints,
                    "tokens": completion.tokens,
                    "duration_s": completion.duration_s,
                    "error": completion.error,
                })
        except Exception as e:
            results.append({"file_path": fi.path, "error": str(e)})

    duration = time.time() - start_time
    writer = NightlyReportWriter(output_dir=config["output_dir"])
    routing_meta = _extract_routing_metadata(executor)
    report_path = writer.write(
        task_name="code_completion",
        results=results,
        metadata={
            "files_count": len(files), "duration_s": duration,
            "total_markers": total_markers,
            **routing_meta,
        },
    )
    _write_audit("code_completion", f"{len(files)} files, {total_markers} markers")

    result = TaskResult.ok(
        message=f"代码补全完成: {len(files)} 文件, {total_markers} 个标记, "
                f"耗时 {duration:.0f}s",
        data={"files_processed": len(files), "markers_found": total_markers,
              "report_path": report_path, "duration_s": duration},
    )
    _record_task_result("code_completion", True, config)
    return result


# ══════════════════════════════════════════════════════════════
# 任务 3: 测试生成 + 执行验证 + 覆盖率（14:00）
# ══════════════════════════════════════════════════════════════

@register_task(
    name="test_generation",
    trigger="cron",
    hour=3,
    minute=30,
    description="测试生成 + 执行验证 + 覆盖率采集",
    idempotency_key="{date}-testgen",
    timeout=1800,
    max_retries=1,
)
def test_generation(context: TaskContext):
    """测试生成（v2 — 生成即执行）"""
    config = _get_config(context)
    start_time = time.time()

    # 熔断器检查（Layer 3）
    cb_result = _check_circuit("test_generation", config)
    if cb_result:
        return cb_result

    ollama_fail = _connect_ollama(config, "test_generation")
    if ollama_fail:
        return ollama_fail

    runner_mod, defect_mod, coverage_mod = _try_import_test_tools()

    # 扫描目标文件（排除已有测试的文件）
    scanner = TargetScanner(
        target_dirs=config["target_dirs"],
        exclude_patterns=config.get("exclude_patterns", []) + ["test_*.py", "_*.py"],
    )
    all_files = scanner.scan()
    if config.get("incremental"):
        last_run = _get_last_run_time(config["execution_log"])
        files = scanner.filter_changed(all_files, last_run) if last_run else all_files
    else:
        files = all_files

    if not files:
        return TaskResult.ok(message="无目标文件", data={"files_count": 0})

    executor = _get_routed_executor(config)
    results: List[Dict] = []
    tests_passed = 0
    tests_failed = 0
    tests_saved = 0

    for fi in files:
        outcome = _generate_test_for_file(fi, executor, runner_mod)
        results.append(outcome["result"])
        tests_passed += outcome["passed"]
        tests_failed += outcome["failed"]
        tests_saved += outcome["saved"]

    # 覆盖率采集（对有测试的项目）
    coverage_data = _collect_coverage(config, coverage_mod)

    duration = time.time() - start_time
    writer = NightlyReportWriter(output_dir=config["output_dir"])
    routing_meta = _extract_routing_metadata(executor)
    report_path = writer.write(
        task_name="test_generation",
        results=results,
        metadata={
            "files_count": len(files), "duration_s": duration,
            "tests_passed": tests_passed, "tests_failed": tests_failed,
            "tests_saved": tests_saved, "coverage": coverage_data,
            **routing_meta,
        },
    )
    _write_audit("test_generation",
                 f"{len(files)} files, {tests_passed} pass, {tests_failed} fail")

    result = TaskResult.ok(
        message=f"测试生成完成: {len(files)} 文件, "
                f"通过 {tests_passed}/失败 {tests_failed}/保存 {tests_saved}, "
                f"耗时 {duration:.0f}s",
        data={
            "files_processed": len(files),
            "tests_passed": tests_passed, "tests_failed": tests_failed,
            "tests_saved": tests_saved, "coverage": coverage_data,
            "report_path": report_path, "duration_s": duration,
        },
    )
    _record_task_result("test_generation", True, config)
    return result


def _generate_test_for_file(fi: Any, executor: Any, runner_mod: Optional[Any]) -> Dict[str, Any]:
    """为单个源文件生成测试并执行验证。

    返回聚合结果：{"result": <写入报告的结果 dict>,
                   "passed": int, "failed": int, "saved": int}
    passed/failed/saved 为 0/1 计数增量，供调用方累加；
    生成失败或异常路径不产生计数增量（与重构前行为一致）。
    """
    try:
        with open(fi.path, "r", encoding="utf-8") as f:
            source = f.read()

        # 检查同目录是否已有测试文件
        existing_tests = _find_existing_tests(fi.path)
        existing_summary = "\n".join(existing_tests) if existing_tests else ""

        test_gen = executor.generate_tests(source, fi.path,
                                            existing_tests=existing_summary)
        if not test_gen.test_code or test_gen.error:
            return {
                "result": {
                    "file_path": fi.path, "test_code": "",
                    "test_result": "生成失败",
                    "error": test_gen.error,
                },
                "passed": 0, "failed": 0, "saved": 0,
            }

        # 写入临时文件并执行验证
        test_result = _execute_generated_test(
            fi.path, test_gen.test_code, runner_mod,
        )

        result = {
            "file_path": fi.path,
            "test_code": test_gen.test_code[:1000],  # 截断
            "test_targets": test_gen.test_targets,
            "test_count": test_gen.test_count,
            "test_result": test_result["status"],
            "test_passed": test_result.get("passed", 0),
            "test_failed": test_result.get("failed", 0),
            "tokens": test_gen.tokens,
            "duration_s": test_gen.duration_s,
            "error": test_gen.error,
        }

        passed = failed = saved = 0
        if test_result["status"] == "PASS":
            passed = 1
            # 测试通过 → 保存到项目测试目录
            saved_path = _save_test_file(fi.path, test_gen.test_code)
            if saved_path:
                saved = 1
                result["saved_to"] = saved_path
        else:
            failed = 1

        return {"result": result, "passed": passed, "failed": failed, "saved": saved}

    except Exception as e:
        return {
            "result": {"file_path": fi.path, "test_code": "", "error": str(e)},
            "passed": 0, "failed": 0, "saved": 0,
        }


def _collect_coverage(config: Dict[str, Any], coverage_mod: Optional[Any]) -> Dict[str, Any]:
    """对有测试的项目采集覆盖率；coverage_mod 不可用时返回空 dict。"""
    coverage_data: Dict[str, Any] = {}
    if not coverage_mod:
        return coverage_data
    try:
        # 从 target_dirs 推导项目路径
        project_dirs = set()
        for td in config["target_dirs"]:
            expanded = os.path.expanduser(td)
            # 向上找到项目根目录（含 pyproject.toml 或 setup.py）
            _p = expanded
            for _ in range(5):
                if os.path.isfile(os.path.join(_p, "pyproject.toml")) or \
                   os.path.isfile(os.path.join(_p, "setup.py")):
                    project_dirs.add(_p)
                    break
                _parent = os.path.dirname(_p)
                if _parent == _p:
                    break
                _p = _parent

        for pd in project_dirs:
            cov = coverage_mod.collect_coverage(pd, timeout=120)
            coverage_data[cov.project_name] = {
                "rate": cov.coverage_rate,
                "total": cov.total_lines,
                "covered": cov.covered_lines,
            }
    except Exception as e:
        logger.warning("覆盖率采集失败: %s", e)
    return coverage_data


def _find_existing_tests(source_path: str) -> List[str]:
    """查找同目录下的已有测试文件"""
    source_dir = os.path.dirname(source_path)
    source_name = os.path.splitext(os.path.basename(source_path))[0]
    tests = []

    # 查找 test_<name>.py 或 tests/ 目录
    candidates = [
        os.path.join(source_dir, f"test_{source_name}.py"),
        os.path.join(source_dir, "tests", f"test_{source_name}.py"),
        os.path.join(os.path.dirname(source_dir), "tests", f"test_{source_name}.py"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            try:
                with open(c, "r", encoding="utf-8") as f:
                    content = f.read()
                # 提取测试函数名
                for m in re.finditer(r"def (test_\w+)", content):
                    tests.append(m.group(1))
            except Exception:
                pass
    return tests


def _execute_generated_test(
    source_path: str, test_code: str,
    runner_mod: Optional[Any] = None,
) -> Dict[str, Any]:
    """将生成的测试写入临时文件并执行"""
    source_dir = os.path.dirname(source_path)
    source_name = os.path.splitext(os.path.basename(source_path))[0]

    # 写入临时测试文件
    tmp_dir = tempfile.mkdtemp(prefix="nightly_test_")
    test_file = os.path.join(tmp_dir, f"test_{source_name}.py")
    try:
        with open(test_file, "w", encoding="utf-8") as f:
            f.write(test_code)

        if runner_mod:
            # 使用 dev-test-tools 执行
            result = runner_mod.run_tests(source_dir, test_path=test_file, timeout=60)
            return {
                "status": "PASS" if result.is_success else "FAIL",
                "passed": result.passed,
                "failed": result.failed,
                "stdout": result.stdout[:500],
            }
        else:
            # 回退：直接用 subprocess 执行
            import subprocess
            r = subprocess.run(
                [sys.executable, "-m", "pytest", test_file, "--tb=short", "-q"],
                capture_output=True, text=True, timeout=60,
                cwd=source_dir,
            )
            return {
                "status": "PASS" if r.returncode == 0 else "FAIL",
                "passed": r.stdout.count(" passed") or 0,
                "stdout": r.stdout[:500],
            }
    except Exception as e:
        return {"status": "ERROR", "error": str(e)}
    finally:
        # 清理临时文件
        try:
            os.remove(test_file)
            os.rmdir(tmp_dir)
        except Exception:
            pass


def _save_test_file(source_path: str, test_code: str) -> Optional[str]:
    """将通过验证的测试文件保存到项目测试目录"""
    source_dir = os.path.dirname(source_path)
    source_name = os.path.splitext(os.path.basename(source_path))[0]
    test_filename = f"test_{source_name}.py"

    # 优先保存到 tests/ 子目录
    tests_dir = os.path.join(source_dir, "tests")
    if not os.path.isdir(tests_dir):
        tests_dir = source_dir

    target = os.path.join(tests_dir, test_filename)
    # 不覆盖已有测试
    if os.path.isfile(target):
        return None

    try:
        with open(target, "w", encoding="utf-8") as f:
            f.write(test_code)
        return target
    except Exception as e:
        logger.warning("测试文件保存失败: %s", e)
        return None


# ══════════════════════════════════════════════════════════════
# 任务 4: 质量趋势汇总（04:45）
# ══════════════════════════════════════════════════════════════

@register_task(
    name="quality_report",
    trigger="cron",
    hour=6,
    minute=0,
    description="质量趋势汇总报告",
    idempotency_key="{date}-quality",
    timeout=300,
    max_retries=0,
)
def quality_report(context: TaskContext):
    """汇总当日质量指标，生成趋势报告"""
    config = _get_config(context)
    try:
        from .quality_tracker import QualityTracker
        tracker = QualityTracker(output_dir=config["output_dir"])
        trend = tracker.get_trend(days=7)
        report_path = tracker.write_trend_report()
        return TaskResult.ok(
            message=f"质量趋势报告已生成: {report_path}",
            data={"trend": trend, "report_path": report_path},
        )
    except ImportError:
        return TaskResult.ok(message="quality_tracker 不可用", data={})
