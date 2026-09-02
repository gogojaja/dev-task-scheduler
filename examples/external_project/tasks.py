"""
外部项目示例：展示如何接入 dev-task-scheduler 框架

使用方式：
    1. 安装: pip install dev-task-scheduler
    2. 创建 tasks.py（本文件）
    3. 创建 scheduler.yaml 配置文件
    4. 启动: dev-task-scheduler start --config scheduler.yaml --tasks-dir .
"""

import time
from scheduler.registry import TaskRegistry, register_task
from scheduler.models import TaskDefinition, TriggerType, TaskResult


# ─── 方式一：独立装饰器注册 ──────────────────────────

@register_task(
    name="daily_backup",
    trigger="cron",
    hour=2,
    minute=0,
    description="每日凌晨 2 点执行数据库备份",
    timeout=600,
    max_retries=3,
)
def daily_backup():
    """模拟数据库备份"""
    print("[daily_backup] 开始备份...")
    time.sleep(1)
    print("[daily_backup] 备份完成")
    return TaskResult.ok(data={"backup_file": "backup_20260902.sql"})


@register_task(
    name="hourly_health_check",
    trigger="interval",
    hours=1,
    description="每小时健康检查",
    timeout=30,
)
def hourly_health_check():
    """模拟健康检查"""
    print("[health_check] 检查服务状态...")
    services_ok = True
    return TaskResult.ok(data={"services_ok": services_ok})


@register_task(
    name="weekly_report",
    trigger="cron",
    day_of_week="mon",
    hour=8,
    minute=0,
    description="每周一早 8 点生成周报",
    timeout=300,
)
def weekly_report():
    """模拟周报生成"""
    print("[weekly_report] 生成周报...")
    time.sleep(0.5)
    return TaskResult.ok(data={"report": "weekly_report_2026-W35.pdf"})


# ─── 方式二：编程式注册 ───────────────────────────────

def sync_user_data():
    """同步用户数据"""
    print("[sync_user] 同步用户数据...")
    time.sleep(0.5)
    return TaskResult.ok(data={"synced": 42})


registry = TaskRegistry()
registry.register(
    TaskDefinition(
        name="sync_user_data",
        func_ref="examples.external_project.tasks:sync_user_data",
        trigger_type=TriggerType.INTERVAL,
        trigger_config={"minutes": 30},
        description="每 30 分钟同步用户数据",
        timeout=120,
        max_retries=2,
    ),
)
