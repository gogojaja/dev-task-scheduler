"""环境预检 + 自愈模块（v1 — 四层保护之 Layer 1）

在任务执行前自动检查环境依赖，缺失的尝试自动修复。
检查项：Python 包 / 项目路径 / Ollama / 磁盘空间 / Git 仓库。
"""
from __future__ import annotations

import csv
import importlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class CheckResult:
    name: str
    status: str  # PASS / WARN / FAIL
    detail: str = ""
    auto_fixed: bool = False


@dataclass
class HealthReport:
    date: str = ""
    checks: List[CheckResult] = field(default_factory=list)

    @property
    def all_pass(self) -> bool:
        return all(c.status == "PASS" for c in self.checks)

    @property
    def has_failure(self) -> bool:
        return any(c.status == "FAIL" for c in self.checks)

    def summary(self) -> Dict[str, int]:
        s = {"PASS": 0, "WARN": 0, "FAIL": 0}
        for c in self.checks:
            s[c.status] = s.get(c.status, 0) + 1
        return s


# ── 检查函数 ──

def check_python_packages(required: List[str]) -> List[CheckResult]:
    """检查 Python 包是否已安装，缺失则自动 pip install --user"""
    results = []
    for pkg in required:
        import_name = pkg.replace("-", "_")
        try:
            importlib.import_module(import_name)
            results.append(CheckResult(name=f"pkg:{pkg}", status="PASS"))
        except ImportError:
            # 尝试自动安装
            try:
                rc = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--user", "--quiet", pkg],
                    capture_output=True, timeout=120,
                )
                if rc.returncode == 0:
                    results.append(CheckResult(
                        name=f"pkg:{pkg}", status="PASS",
                        detail="auto-installed", auto_fixed=True,
                    ))
                else:
                    results.append(CheckResult(
                        name=f"pkg:{pkg}", status="FAIL",
                        detail=f"install failed: {rc.stderr.decode()[:100]}",
                    ))
            except Exception as e:
                results.append(CheckResult(
                    name=f"pkg:{pkg}", status="FAIL",
                    detail=f"install error: {str(e)[:100]}",
                ))
    return results


def check_paths(configured_paths: Dict[str, str]) -> List[CheckResult]:
    """验证环境变量中的路径是否存在"""
    results = []
    for name, path in configured_paths.items():
        path = os.path.expanduser(path)
        if os.path.isdir(path):
            results.append(CheckResult(name=f"path:{name}", status="PASS"))
        else:
            results.append(CheckResult(
                name=f"path:{name}", status="FAIL",
                detail=f"{path} not found",
            ))
    return results


def check_ollama(host: str = "127.0.0.1", port: int = 11434,
                 required_models: Optional[List[str]] = None) -> List[CheckResult]:
    """验证 Ollama 可达 + 目标模型已拉取"""
    results = []
    url = f"http://{host}:{port}/api/tags"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "EnvHealth/1.0"})
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode("utf-8"))
        results.append(CheckResult(name="ollama:reachable", status="PASS"))

        # 检查模型
        available = {m.get("name", "") for m in data.get("models", [])}
        for model in (required_models or ["qwen2.5-coder:14b"]):
            if any(model in m for m in available):
                results.append(CheckResult(name=f"ollama:model:{model}", status="PASS"))
            else:
                results.append(CheckResult(
                    name=f"ollama:model:{model}", status="WARN",
                    detail="model not pulled yet",
                ))
    except Exception as e:
        results.append(CheckResult(
            name="ollama:reachable", status="FAIL",
            detail=str(e)[:100],
        ))
    return results


def check_disk_space(path: str = "~", min_mb: int = 500) -> List[CheckResult]:
    """检查磁盘剩余空间"""
    path = os.path.expanduser(path)
    try:
        usage = shutil.disk_usage(path)
        free_mb = usage.free // (1024 * 1024)
        if free_mb >= min_mb:
            return [CheckResult(
                name="disk:space", status="PASS",
                detail=f"{free_mb}MB free",
            )]
        else:
            return [CheckResult(
                name="disk:space", status="FAIL",
                detail=f"only {free_mb}MB free (need {min_mb}MB)",
            )]
    except Exception as e:
        return [CheckResult(name="disk:space", status="WARN", detail=str(e)[:100])]


def check_git_repos(repo_dirs: List[str]) -> List[CheckResult]:
    """验证各项目目录是有效 git 仓库"""
    results = []
    for d in repo_dirs:
        d = os.path.expanduser(d)
        name = os.path.basename(d)
        if not os.path.isdir(d):
            results.append(CheckResult(name=f"git:{name}", status="FAIL", detail="dir not found"))
        elif not os.path.isdir(os.path.join(d, ".git")):
            results.append(CheckResult(name=f"git:{name}", status="WARN", detail="not a git repo"))
        else:
            results.append(CheckResult(name=f"git:{name}", status="PASS"))
    return results


# ── 主入口 ──

def run_precheck(config: Dict[str, Any]) -> HealthReport:
    """执行完整环境预检
    
    Args:
        config: 任务配置，可包含以下键：
            - required_packages: List[str] — 需要检查的 Python 包
            - project_paths: Dict[str, str] — 需要检查的路径
            - ollama_host / ollama_port: Ollama 连接信息
            - required_models: List[str] — 需要的 Ollama 模型
            - repo_dirs: List[str] — 项目 git 仓库目录
            - output_dir: 报告输出目录
    """
    report = HealthReport(date=datetime.now().strftime("%Y-%m-%d"))

    # 1. Python 包
    pkgs = config.get("required_packages", ["vulture", "pip-audit"])
    report.checks.extend(check_python_packages(pkgs))

    # 2. 项目路径
    paths = config.get("project_paths", {})
    if not paths:
        # 默认从环境变量读取
        for env_var in ["DEV_MODEL_ROUTER_PATH", "DEV_SECURITY_TOOLS_PATH",
                        "DEV_TEST_TOOLS_PATH", "DEV_PROJECT_MGMT_PATH"]:
            val = os.environ.get(env_var, "")
            if val:
                paths[env_var] = val
    if paths:
        report.checks.extend(check_paths(paths))

    # 3. Ollama
    host = config.get("ollama_host", "127.0.0.1")
    port = config.get("ollama_port", 11434)
    models = config.get("required_models", ["qwen2.5-coder:14b"])
    report.checks.extend(check_ollama(host, port, models))

    # 4. 磁盘空间
    output_dir = config.get("output_dir", "~/projects/TwinForge/docs/nightly_reports/")
    report.checks.extend(check_disk_space(output_dir))

    # 5. Git 仓库
    repo_dirs = config.get("repo_dirs", [])
    if repo_dirs:
        report.checks.extend(check_git_repos(repo_dirs))

    # 写报告
    output_dir = os.path.expanduser(config.get("output_dir", "~/projects/TwinForge/docs/nightly_reports/"))
    _write_report(report, output_dir)

    return report


def _write_report(report: HealthReport, output_dir: str):
    """写入 JSON + CSV + Markdown 报告"""
    os.makedirs(output_dir, exist_ok=True)
    date_str = report.date

    # JSON
    json_path = os.path.join(output_dir, f"{date_str}_env_health.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"date": date_str, "checks": [asdict(c) for c in report.checks],
                    "summary": report.summary()}, f, ensure_ascii=False, indent=2)

    # CSV（追加模式，供趋势追踪）
    csv_path = os.path.join(output_dir, "env_health.csv")
    write_header = not os.path.isfile(csv_path)
    with open(csv_path, "a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["date", "check_name", "status", "detail", "auto_fixed"])
        for c in report.checks:
            writer.writerow([date_str, c.name, c.status, c.detail, c.auto_fixed])

    # Markdown
    md_path = os.path.join(output_dir, f"{date_str}_env_health.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# 环境健康报告 — {date_str}\n\n")
        s = report.summary()
        f.write(f"- PASS: {s['PASS']} | WARN: {s['WARN']} | FAIL: {s['FAIL']}\n\n")
        f.write(f"| 检查项 | 状态 | 说明 | 自愈 |\n")
        f.write(f"|--------|------|------|------|\n")
        for c in report.checks:
            fixed = "YES" if c.auto_fixed else ""
            f.write(f"| {c.name} | {c.status} | {c.detail} | {fixed} |\n")

    print(f"[env_health] 报告: {md_path} ({s['PASS']}P/{s['WARN']}W/{s['FAIL']}F)")
