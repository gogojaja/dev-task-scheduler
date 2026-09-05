"""
夜间项目评审任务

以独立第三方视角，依据行业最佳实践对各项目文档进行评审。
输出三份文档：优化建议方案、修改计划、评审报告。
同时更新待办事项列表，等待用户批准后执行。

调度时间：每日 05:00
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from typing import List, Dict, Any, Optional

# 确保 dev-model-router 在 Python 路径中
ROUTER_PATH = os.environ.get("DEV_MODEL_ROUTER_PATH", "")
if ROUTER_PATH and ROUTER_PATH not in sys.path:
    sys.path.insert(0, ROUTER_PATH)

from ..registry import register_task
from ..models import TaskResult, TaskContext

logger = logging.getLogger(__name__)


# ── 项目注册表 ──

PROJECT_REGISTRY = [
    {
        "name": "TwinForge",
        "description": "双机 AI 工作站（Mac mini + Dell 7010），本地部署架构与涉密信息管理",
        "base_path": "~/projects/TwinForge",
        "documents": [
            {"path": "requirements/SRS_v1.0/04_功能需求.csv", "type": "需求规格"},
            {"path": "requirements/SRS_v1.0/05_性能需求.csv", "type": "性能需求"},
            {"path": "requirements/SRS_v1.0/07_质量属性.csv", "type": "质量属性"},
            {"path": "架构资产/02_架构设计/架构设计文档.md", "type": "架构设计"},
            {"path": "架构资产/03_数据安全架构/数据安全架构设计.md", "type": "安全架构"},
            {"path": "架构资产/04_架构评审/ADR-TwinForge-001.md", "type": "架构决策"},
            {"path": "开发资产/策略/开发策略分析报告.md", "type": "开发策略"},
            {"path": "开发资产/策略/编码规范.md", "type": "编码规范"},
            {"path": "测试资产/策略/测试策略分析报告.md", "type": "测试策略"},
            {"path": "投产资产/策略/投产策略分析报告.md", "type": "投产策略"},
        ],
    },
    {
        "name": "DevProjectTeamSkill",
        "description": "软件研发多角色编排器，编排全生命周期多角色协同（启动/需求/架构/开发/测试/投产/总控/项目群/管理咨询/项目经理）",
        "base_path": "~/projects/DevProjectTeamSkill",
        "documents": [
            {"path": "AGENTS.md", "type": "Agent 指令"},
            {"path": "references/iron_rules.md", "type": "铁律规范"},
            {"path": "references/model_selection.md", "type": "模型选型"},
            {"path": "references/consulting_standards.md", "type": "咨询标准"},
            {"path": "references/traceability_standard.md", "type": "追溯标准"},
            {"path": "references/multi_project_isolation.md", "type": "多项目隔离"},
        ],
    },
    {
        "name": "dev-model-router",
        "description": "多模型分层编排工具——高阶模型拆解任务、低阶模型执行、高阶模型组装（TwinForge 子项目）",
        "base_path": "~/projects/dev-model-router",
        "documents": [
            {"path": "README.md", "type": "项目说明"},
            {"path": "AGENTS.md", "type": "Agent 指令"},
        ],
    },
    {
        "name": "dev-task-scheduler",
        "description": "轻量、可靠、可观测的本地定时任务调度框架（TwinForge 子项目）",
        "base_path": "~/projects/dev-task-scheduler",
        "documents": [
            {"path": "README.md", "type": "项目说明"},
            {"path": "AGENTS.md", "type": "Agent 指令"},
            {"path": "docs/architecture.md", "type": "架构文档"},
            {"path": "docs/api.md", "type": "API 文档"},
            {"path": "docs/usage.md", "type": "使用指南"},
        ],
    },
    {
        "name": "dev-git-hub",
        "description": "Git 基建与仓库管理（DevProjectTeamSkill 子项目）",
        "base_path": "~/projects/dev-git-hub",
        "documents": [
            {"path": "README.md", "type": "项目说明"},
            {"path": "AGENTS.md", "type": "Agent 指令"},
        ],
    },
    {
        "name": "dev-project-mgmt",
        "description": "项目群管理工具——台账、注册表、依赖矩阵（DevProjectTeamSkill 子项目）",
        "base_path": "~/projects/dev-project-mgmt",
        "documents": [
            {"path": "README.md", "type": "项目说明"},
            {"path": "AGENTS.md", "type": "Agent 指令"},
        ],
    },
    {
        "name": "dev-security-tools",
        "description": "安全工具集（DevProjectTeamSkill 子项目）",
        "base_path": "~/projects/dev-security-tools",
        "documents": [
            {"path": "README.md", "type": "项目说明"},
            {"path": "AGENTS.md", "type": "Agent 指令"},
        ],
    },
    {
        "name": "dev-test-tools",
        "description": "测试工具集（DevProjectTeamSkill 子项目）",
        "base_path": "~/projects/dev-test-tools",
        "documents": [
            {"path": "README.md", "type": "项目说明"},
            {"path": "AGENTS.md", "type": "Agent 指令"},
        ],
    },
]


# ── 文档读取 ──

MAX_DOC_CHARS = 3000


def _read_document(base_path: str, doc_path: str) -> str:
    full_path = os.path.expanduser(os.path.join(base_path, doc_path))
    if not os.path.isfile(full_path):
        return f"[文件不存在: {doc_path}]"
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
        if len(content) > MAX_DOC_CHARS:
            content = content[:MAX_DOC_CHARS] + f"\n\n... [截断，原文 {len(content)} 字符]"
        return content
    except Exception as e:
        return f"[读取失败: {e}]"


def _build_documents_content(base_path: str, documents: List[Dict]) -> str:
    parts = []
    for doc in documents:
        content = _read_document(base_path, doc["path"])
        parts.append(f"### {doc['type']}：{doc['path']}\n\n{content}\n")
    return "\n---\n\n".join(parts)


# ── 报告生成 ──

def _write_optimization_report(project_name, findings, output_dir, date_str):
    opt = [f for f in findings if f.get("category") == "optimization"]
    lines = [
        f"# {project_name} 优化建议方案 — {date_str}", "",
        f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 评审依据: 行业最佳实践（IEEE 830 / C4 / OWASP / DORA / PMBOK）", "",
        "## 摘要", "", f"共识别 **{len(opt)}** 项优化机会。", "",
    ]
    if opt:
        lines.append("## 优化建议")
        lines.append("")
        for f in opt:
            lines.append(f"### {f.get('id', '?')}: {f.get('title', '未命名')}")
            lines.append("")
            lines.append(f"- **维度**: {f.get('dimension', '-')}")
            lines.append(f"- **优先级**: {f.get('priority', '-')}")
            lines.append(f"- **工作量**: {f.get('effort', '-')}")
            lines.append(f"- **当前状态**: {f.get('current_state', '-')}")
            lines.append(f"- **行业最佳实践**: {f.get('best_practice', '-')}")
            lines.append(f"- **改进建议**: {f.get('recommendation', '-')}")
            lines.append(f"- **预期收益**: {f.get('benefit', '-')}")
            lines.append("")
    else:
        lines.extend(["当前无优化建议。", ""])
    lines.extend(["---", "*由 dev-model-router + dev-task-scheduler 自动生成，待批准后执行*"])
    path = os.path.join(output_dir, f"{date_str}_{project_name}_优化建议方案.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def _write_modification_plan(project_name, findings, output_dir, date_str):
    mod = [f for f in findings if f.get("category") == "modification"]
    lines = [
        f"# {project_name} 修改计划 — {date_str}", "",
        f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 评审依据: 行业最佳实践（IEEE 830 / C4 / OWASP / DORA / PMBOK）", "",
        "## 摘要", "", f"共识别 **{len(mod)}** 项需要修改的问题。", "",
    ]
    if mod:
        lines.extend(["## 修改项", "", "| 编号 | 维度 | 优先级 | 标题 | 差距 | 建议 | 工作量 |",
                       "|------|------|--------|------|------|------|--------|"])
        for f in mod:
            lines.append(f"| {f.get('id','?')} | {f.get('dimension','-')} | {f.get('priority','-')} "
                         f"| {f.get('title','-')} | {f.get('gap','-')[:60]} "
                         f"| {f.get('recommendation','-')[:60]} | {f.get('effort','-')} |")
        lines.append("")
        lines.extend(["## 执行计划", ""])
        prio_ord = {"高": 0, "中": 1, "低": 2}
        for i, f in enumerate(sorted(mod, key=lambda x: prio_ord.get(x.get("priority","低"), 3)), 1):
            lines.append(f"{i}. **[{f.get('priority','?')}]** {f.get('title','?')} — {f.get('recommendation','')}")
        lines.append("")
    else:
        lines.extend(["当前无需修改项。", ""])
    lines.extend(["---", "*由 dev-model-router + dev-task-scheduler 自动生成，待批准后执行*"])
    path = os.path.join(output_dir, f"{date_str}_{project_name}_修改计划.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def _write_review_report(project_name, findings, summary, docs_reviewed, output_dir, date_str):
    lines = [
        f"# {project_name} 项目评审报告 — {date_str}", "",
        f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "> 评审视角: 独立第三方评审",
        "> 评审依据: 行业最佳实践（IEEE 830 / C4 / OWASP / DORA / PMBOK）", "",
        "## 评审概览", "",
        f"- **项目**: {project_name}",
        f"- **评审文档数**: {len(docs_reviewed)}",
        f"- **综合评分**: {summary.get('overall_score', '-')}",
        f"- **成熟度**: {summary.get('maturity_level', '-')}",
        f"- **发现总数**: {summary.get('total_findings', len(findings))}",
        f"  - 高优先级: {summary.get('high_priority', 0)}",
        f"  - 中优先级: {summary.get('medium_priority', 0)}",
        f"  - 低优先级: {summary.get('low_priority', 0)}",
        f"- **分类**: 优化 {summary.get('optimization_count',0)} / "
        f"修改 {summary.get('modification_count',0)} / "
        f"升级 {summary.get('upgrade_count',0)}", "",
        "## 评审文档清单", "",
    ]
    for doc in docs_reviewed:
        lines.append(f"- {doc.get('type', '?')}: `{doc.get('path', '?')}`")
    lines.append("")
    if findings:
        lines.extend(["## 评审发现", ""])
        by_dim: Dict[str, List] = {}
        for f in findings:
            by_dim.setdefault(f.get("dimension", "其他"), []).append(f)
        for dim, df in by_dim.items():
            lines.append(f"### {dim}")
            lines.append("")
            for f in df:
                icon = {"高":"🔴","中":"🟡","低":"🟢"}.get(f.get("priority",""),"⚪")
                lines.append(f"#### {icon} {f.get('id','?')}: {f.get('title','?')}")
                lines.append("")
                lines.append(f"- **优先级**: {f.get('priority','-')} | **类别**: {f.get('category','-')} | **工作量**: {f.get('effort','-')}")
                lines.append(f"- **当前状态**: {f.get('current_state','-')}")
                lines.append(f"- **最佳实践**: {f.get('best_practice','-')}")
                lines.append(f"- **差距**: {f.get('gap','-')}")
                lines.append(f"- **建议**: {f.get('recommendation','-')}")
                lines.append(f"- **收益**: {f.get('benefit','-')}")
                lines.append("")
    else:
        lines.extend(["未发现评审问题。", ""])
    lines.extend([
        "## 结论", "",
        f"综合评分 **{summary.get('overall_score','-')}**，成熟度 **{summary.get('maturity_level','-')}**。",
        "", "---", "*由 dev-model-router + dev-task-scheduler 自动生成，待批准后执行*",
    ])
    path = os.path.join(output_dir, f"{date_str}_{project_name}_评审报告.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def _update_todo_list(project_name, findings, todo_dir, date_str):
    os.makedirs(todo_dir, exist_ok=True)
    todo_path = os.path.join(todo_dir, "pending_review_todos.md")
    items = []
    for f in findings:
        # 兼容两种格式：完整评审(priority) 和 轻量评审(severity)
        p = f.get("priority") or {"error": "高", "warning": "中", "info": "低"}.get(f.get("severity", ""), "低")
        icon = {"高":"🔴","中":"🟡","低":"🟢"}.get(p,"⚪")
        title = f.get("title") or f.get("description", "?")
        items.append(
            f"- [ ] {icon} **[{project_name}]** {title} "
            f"(优先级:{p}, 类别:{f.get('category', f.get('check_type', '-'))}, "
            f"工作量:{f.get('effort','-')}) — {f.get('recommendation', f.get('description', ''))[:80]}"
        )
    content = f"\n## 评审待办 — {date_str} ({project_name})\n\n" + "\n".join(items) + "\n"
    with open(todo_path, "a", encoding="utf-8") as f:
        f.write(content)
    return todo_path


# ── JSON 解析 ──

def _parse_review_json(text: str) -> Dict[str, Any]:
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "findings" in data:
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    match = re.search(r"```(?:json)?\s*\n(\{.*?\})\n```", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict) and "findings" in data:
                return data
        except (json.JSONDecodeError, TypeError):
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            if isinstance(data, dict) and "findings" in data:
                return data
        except (json.JSONDecodeError, TypeError):
            pass
    logger.warning("无法解析评审结果为 JSON")
    return {"findings": [], "summary": {}, "documents_reviewed": []}


# ── 跨项目汇总 ──

def _write_cross_project_summary(all_reports, output_dir, date_str):
    lines = [
        f"# 夜间项目评审汇总 — {date_str}", "",
        f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "",
        "## 各项目评审概览", "",
        "| 项目 | 评分 | 发现数 | 高优先级 | 报告 |",
        "|------|------|--------|----------|------|",
    ]
    for r in all_reports:
        lines.append(f"| {r['project']} | {r['overall_score']} | {r['findings_count']} "
                     f"| {r['high_priority']} | [评审报告]({os.path.basename(r['reports']['review'])}) |")
    total = sum(r["findings_count"] for r in all_reports)
    high = sum(r["high_priority"] for r in all_reports)
    lines.extend([
        "", "## 汇总统计", "",
        f"- 评审项目: {len(all_reports)} 个",
        f"- 发现总数: {total} 项",
        f"- 高优先级: {high} 项", "",
        "## 待办事项", "",
        "请查阅各项目报告后，批准需要执行的改进项。",
        "待办事项已汇总至 `pending_review_todos.md`。", "",
        "---", "*由 dev-model-router + dev-task-scheduler 自动生成*",
    ])
    path = os.path.join(output_dir, f"{date_str}_项目评审汇总.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


# ── 主任务 ──

def _nightly_project_review_impl(context: TaskContext):
    """夜间项目评审"""
    config = {
        "review_mode": context.params.get("review_mode", "lightweight"),
        "output_dir": context.params.get("output_dir", "~/projects/TwinForge/docs/nightly_reports/"),
        "model": context.params.get("model", "qwen2.5-coder:14b"),
        "ollama_host": context.params.get("ollama_host", "127.0.0.1"),
        "ollama_port": context.params.get("ollama_port", 11434),
        "todo_dir": context.params.get("todo_dir", "~/projects/TwinForge/docs/nightly_reports/"),
        "cloud_api_base_url": context.params.get("cloud_api_base_url", ""),
        "cloud_api_key": context.params.get("cloud_api_key", ""),
        "cloud_api_model": context.params.get("cloud_api_model", ""),
    }
    output_dir = os.path.expanduser(config["output_dir"])
    todo_dir = os.path.expanduser(config["todo_dir"])
    os.makedirs(output_dir, exist_ok=True)

    start_time = time.time()
    date_str = datetime.now().strftime("%Y%m%d")

    # ── Ollama 连接（所有模式都需要）──
    try:
        from executor.ollama_client import OllamaClient
        client = OllamaClient(host=config["ollama_host"], port=config["ollama_port"])
        if not client.is_available():
            return TaskResult.fail(message="Ollama 服务不可用", error_code="SCH-REVIEW-001", skip_retry=True)
    except Exception as e:
        return TaskResult.fail(message=f"Ollama 连接失败: {e}", error_code="SCH-REVIEW-002")

    # ── 模式路由 ──
    mode = config["review_mode"]
    logger.info("评审模式: %s", mode)

    if mode == "lightweight":
        result = _run_lightweight_review(
            client, config, PROJECT_REGISTRY, output_dir, todo_dir, date_str,
        )
    elif mode == "cascade":
        result = _run_cascade_review(
            client, config, PROJECT_REGISTRY, output_dir, todo_dir, date_str,
        )
    else:  # original
        result = _run_original_review(
            client, config, PROJECT_REGISTRY, output_dir, todo_dir, date_str,
        )

    duration = time.time() - start_time
    result["duration_s"] = duration
    result["review_mode"] = mode

    return TaskResult.ok(
        message=f"[{mode}] 项目评审完成: {result.get('projects_reviewed', 0)} 项目, "
                f"{result.get('total_findings', 0)} 项发现, 耗时 {duration:.0f}s",
        data=result,
    )


# ── Mode A: 轻量评审（14B 逐文档结构/一致性/完整性检查）──

def _run_lightweight_review(client, config, projects, output_dir, todo_dir, date_str):
    """轻量评审：14B 做结构校验 + 引用一致性 + 完整性清单"""
    from executor.prompts.lightweight_review import (
        SYSTEM_PROMPT, USER_TEMPLATE, get_checklist,
    )

    all_reports = []
    total_findings = 0

    for project in projects:
        pname = project["name"]
        logger.info("[轻量] 开始评审项目: %s", pname)
        project_findings = []

        for doc_spec in project["documents"]:
            doc_content = _read_document(
                project["base_path"], doc_spec["path"],
            )
            if doc_content.startswith("[文件不存在") or doc_content.startswith("[读取失败"):
                project_findings.append({
                    "check_type": "completeness",
                    "severity": "error",
                    "location": doc_spec["path"],
                    "description": doc_content,
                    "source": "local",
                })
                continue

            checklist = get_checklist(doc_spec["type"])
            prompt = USER_TEMPLATE.format(
                document_path=doc_spec["path"],
                document_type=doc_spec["type"],
                checklist=checklist,
                document_content=doc_content,
            )
            try:
                resp = client.generate(
                    model=config["model"], prompt=prompt,
                    system=SYSTEM_PROMPT, task_type="document_review",
                )
                parsed = _parse_review_json(resp.text)
                findings = parsed.get("findings", [])
                for f in findings:
                    f["source"] = "local"
                project_findings.extend(findings)
            except Exception as e:
                logger.warning("[轻量] 文档扫描失败 [%s/%s]: %s", pname, doc_spec["path"], e)

        # 写轻量报告
        rev_path = _write_lightweight_report(pname, project_findings, output_dir, date_str)
        todo_path = _update_todo_list(pname, project_findings, todo_dir, date_str)

        errors = sum(1 for f in project_findings if f.get("severity") == "error")
        warnings = sum(1 for f in project_findings if f.get("severity") == "warning")
        total_findings += len(project_findings)
        all_reports.append({
            "project": pname, "findings_count": len(project_findings),
            "high_priority": errors,
            "overall_score": "-",
            "reports": {"lightweight": rev_path, "todo": todo_path},
        })
        logger.info("[轻量] 项目 %s 评审完成: %d 项发现 (error=%d, warning=%d)",
                     pname, len(project_findings), errors, warnings)

    summary_path = _write_cross_project_summary(all_reports, output_dir, date_str)
    return {
        "projects_reviewed": len(projects), "total_findings": total_findings,
        "reports": all_reports, "summary_report": summary_path,
    }


# ── Mode B: 级联评审（14B Phase1 + 云端 Phase2）──

def _run_cascade_review(client, config, projects, output_dir, todo_dir, date_str):
    """级联评审：Phase1 轻量扫描 → Phase2 云端深度评审"""
    from executor.cloud_client import CloudClient
    from executor.staged_review import StagedReviewCoordinator

    cloud = CloudClient(
        base_url=config.get("cloud_api_base_url", ""),
        api_key=config.get("cloud_api_key", ""),
        model=config.get("cloud_api_model", ""),
    )

    coordinator = StagedReviewCoordinator(
        ollama_client=client,
        cloud_client=cloud if cloud.is_configured else None,
        local_model=config["model"],
    )

    all_reports = []
    total_findings = 0

    for project in projects:
        pname = project["name"]
        logger.info("[级联] 开始评审项目: %s", pname)

        # 读取文档
        documents = []
        for doc_spec in project["documents"]:
            content = _read_document(project["base_path"], doc_spec["path"])
            documents.append({
                "path": doc_spec["path"],
                "type": doc_spec["type"],
                "content": content,
            })

        # 执行级联评审
        try:
            result = coordinator.run(
                project_name=pname,
                documents=documents,
                project_description=project["description"],
            )
        except Exception as e:
            logger.error("[级联] 项目 %s 评审失败: %s", pname, e)
            continue

        # 写报告
        findings = result.merged_findings
        summary = result.merged_summary
        docs_reviewed = [{"path": d["path"], "type": d["type"]} for d in documents]

        # Phase 1 轻量报告
        lw_path = _write_lightweight_report(
            pname, result.phase1_findings, output_dir, date_str,
        )
        # 完整评审报告
        rev_path = _write_review_report(
            pname, findings, summary, docs_reviewed, output_dir, date_str,
        )
        opt_path = _write_optimization_report(pname, findings, output_dir, date_str)
        mod_path = _write_modification_plan(pname, findings, output_dir, date_str)
        todo_path = _update_todo_list(pname, findings, todo_dir, date_str)

        # Phase 2 降级标记
        if result.phase2_degraded:
            logger.warning("[级联] 项目 %s Phase 2 降级: %s", pname, result.phase2_error)

        total_findings += len(findings)
        all_reports.append({
            "project": pname, "findings_count": len(findings),
            "high_priority": summary.get("high_priority", 0),
            "overall_score": summary.get("overall_score", "-"),
            "phase2_degraded": result.phase2_degraded,
            "reports": {
                "lightweight": lw_path, "review": rev_path,
                "optimization": opt_path, "modification": mod_path,
                "todo": todo_path,
            },
        })
        logger.info("[级联] 项目 %s 评审完成: %d 项发现 (P1=%d P2=%d 降级=%s)",
                     pname, len(findings), len(result.phase1_findings),
                     len(result.phase2_findings), result.phase2_degraded)

    summary_path = _write_cross_project_summary(all_reports, output_dir, date_str)
    return {
        "projects_reviewed": len(projects), "total_findings": total_findings,
        "reports": all_reports, "summary_report": summary_path,
    }


# ── Mode C: 原始评审（向后兼容）──

def _run_original_review(client, config, projects, output_dir, todo_dir, date_str):
    """原始评审：14B 全量评审（现有行为）"""
    from executor.prompts.document_review import SYSTEM_PROMPT, USER_TEMPLATE

    all_reports = []
    total_findings = 0

    for project in projects:
        pname = project["name"]
        logger.info("[原始] 开始评审项目: %s", pname)

        docs_content = _build_documents_content(project["base_path"], project["documents"])
        prompt = USER_TEMPLATE.format(
            project_name=pname,
            project_description=project["description"],
            documents_content=docs_content,
        )

        try:
            resp = client.generate(
                model=config["model"], prompt=prompt,
                system=SYSTEM_PROMPT, task_type="document_review",
            )
            result = _parse_review_json(resp.text)
        except Exception as e:
            logger.error("项目 %s 评审失败: %s", pname, e)
            result = {"findings": [], "summary": {}, "documents_reviewed": []}

        findings = result.get("findings", [])
        summary = result.get("summary", {})
        docs_reviewed = result.get("documents_reviewed", project["documents"])

        opt_path = _write_optimization_report(pname, findings, output_dir, date_str)
        mod_path = _write_modification_plan(pname, findings, output_dir, date_str)
        rev_path = _write_review_report(pname, findings, summary, docs_reviewed, output_dir, date_str)
        todo_path = _update_todo_list(pname, findings, todo_dir, date_str)

        total_findings += len(findings)
        all_reports.append({
            "project": pname, "findings_count": len(findings),
            "high_priority": summary.get("high_priority", 0),
            "overall_score": summary.get("overall_score", "-"),
            "reports": {"optimization": opt_path, "modification": mod_path,
                        "review": rev_path, "todo": todo_path},
        })
        logger.info("项目 %s 评审完成: %d 项发现", pname, len(findings))

    summary_path = _write_cross_project_summary(all_reports, output_dir, date_str)
    return {
        "projects_reviewed": len(projects), "total_findings": total_findings,
        "reports": all_reports, "summary_report": summary_path,
    }


# ── 轻量报告写入 ──

def _write_lightweight_report(project_name, findings, output_dir, date_str):
    """写轻量评审报告（Mode A/B Phase1）"""
    errors = [f for f in findings if f.get("severity") == "error"]
    warnings = [f for f in findings if f.get("severity") == "warning"]
    infos = [f for f in findings if f.get("severity") == "info"]

    lines = [
        f"# {project_name} 轻量评审报告 — {date_str}", "",
        f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "> 评审模式: 轻量评审（结构校验 + 引用一致性 + 完整性清单）", "",
        "## 概览", "",
        f"- **发现总数**: {len(findings)}",
        f"  - 🔴 Error: {len(errors)}",
        f"  - 🟡 Warning: {len(warnings)}",
        f"  - 🟢 Info: {len(infos)}", "",
    ]

    if errors:
        lines.extend(["## 🔴 Error（必须修复）", ""])
        for f in errors:
            lines.append(f"- **[{f.get('check_type', '?')}]** {f.get('location', '?')}: "
                         f"{f.get('description', '')}")
        lines.append("")

    if warnings:
        lines.extend(["## 🟡 Warning（建议修复）", ""])
        for f in warnings:
            lines.append(f"- **[{f.get('check_type', '?')}]** {f.get('location', '?')}: "
                         f"{f.get('description', '')}")
        lines.append("")

    if infos:
        lines.extend(["## 🟢 Info（可选改进）", ""])
        for f in infos:
            lines.append(f"- **[{f.get('check_type', '?')}]** {f.get('location', '?')}: "
                         f"{f.get('description', '')}")
        lines.append("")

    if not findings:
        lines.extend(["未发现评审问题。", ""])

    lines.extend(["---", "*由 dev-model-router + dev-task-scheduler 自动生成*"])
    path = os.path.join(output_dir, f"{date_str}_{project_name}_轻量评审报告.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


# ── 任务注册：14B 轻量评审（每日 18:00）──

@register_task(
    name="nightly_project_review_14b",
    trigger="cron",
    hour=18,
    minute=0,
    description="14B 轻量评审（本地模型，每日 18:00）",
    idempotency_key="{date}-14b",
    timeout=3600,
    max_retries=1,
)
def nightly_project_review_14b(context: TaskContext):
    """14B 轻量评审：强制 lightweight 模式"""
    if hasattr(context, 'params') and isinstance(context.params, dict):
        context.params["review_mode"] = "lightweight"
    return _nightly_project_review_impl(context)


# ── 任务注册：cascade 级联评审（每日 02:00，折扣时段）──

@register_task(
    name="nightly_project_review_cascade",
    trigger="cron",
    hour=2,
    minute=0,
    description="cascade 级联评审（14B + 折扣云端 API，每日 02:00）",
    idempotency_key="{date}-cascade",
    timeout=3600,
    max_retries=1,
)
def nightly_project_review_cascade(context: TaskContext):
    """cascade 级联评审：使用定价管理器选择折扣时段最优模型"""
    if hasattr(context, 'params') and isinstance(context.params, dict):
        context.params["review_mode"] = "cascade"
        # 使用定价管理器选择当前时段最优云端模型
        try:
            from executor.model_pricing import ModelPricingManager
            mgr = ModelPricingManager()
            best = mgr.select_best_model()
            if best:
                context.params["cloud_api_model"] = best.model_id
                logger.info("cascade 选择模型: %s (%s)", best.display_name, best.model_id)
        except ImportError:
            logger.warning("model_pricing 模块不可用，使用默认模型")
    return _nightly_project_review_impl(context)


# ── 任务注册：免费模型评审（每日 22:00）──

@register_task(
    name="nightly_project_review_free",
    trigger="cron",
    hour=22,
    minute=0,
    description="免费模型评审（22:00 免费时段开始）",
    idempotency_key="{date}-free",
    timeout=3600,
    max_retries=1,
)
def nightly_project_review_free(context: TaskContext):
    """免费模型评审：使用免费 API 模型"""
    if hasattr(context, 'params') and isinstance(context.params, dict):
        context.params["review_mode"] = "cascade"
        # 选择当前可用的免费模型
        try:
            from executor.model_pricing import ModelPricingManager
            mgr = ModelPricingManager()
            free_models = mgr.get_free_models()
            if free_models:
                best_free = free_models[0]
                context.params["cloud_api_model"] = best_free.model_id
                logger.info("免费模型选择: %s (%s)", best_free.display_name, best_free.model_id)
            else:
                logger.warning("当前无免费模型可用，回退到折扣模型")
                best = mgr.select_best_model()
                if best:
                    context.params["cloud_api_model"] = best.model_id
        except ImportError:
            logger.warning("model_pricing 模块不可用，使用默认模型")
    return _nightly_project_review_impl(context)
