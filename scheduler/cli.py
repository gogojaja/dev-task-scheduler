#!/usr/bin/env python3
"""
定时任务系统 - 命令行接口

提供任务管理、调度器控制、状态查询、统计等命令。

用法：
    python tools/scheduler/cli.py start          # 启动调度器
    python tools/scheduler/cli.py stop           # 停止调度器
    python tools/scheduler/cli.py status         # 查看状态
    python tools/scheduler/cli.py list           # 列出任务
    python tools/scheduler/cli.py run <name>     # 手动执行任务
    python tools/scheduler/cli.py show <name>    # 查看任务详情
    python tools/scheduler/cli.py pause <name>   # 暂停任务
    python tools/scheduler/cli.py resume <name>  # 恢复任务
    python tools/scheduler/cli.py history        # 查看执行历史
    python tools/scheduler/cli.py stats          # 查看统计
    python tools/scheduler/cli.py dlq            # 查看死信队列
"""

from __future__ import annotations

import argparse
import sys
import os
import json
from datetime import datetime
from pathlib import Path

# 确保项目根目录在 sys.path 中（cli.py 在 tools/scheduler/ 下，往上两级是项目根）
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def cmd_start(args):
    """启动调度器"""
    from tools.scheduler import get_scheduler, config
    from tools.scheduler.registry import task_registry

    # 加载配置
    if args.config:
        config.load_config_from_yaml(args.config)

    # 加载任务
    if args.tasks_dir:
        task_registry.load_from_directory(args.tasks_dir)
    if args.tasks_yaml:
        task_registry.load_from_yaml(args.tasks_yaml)

    scheduler = get_scheduler()
    success = scheduler.start()

    if success:
        print("✅ Scheduler started successfully")
        print(f"   Version: {scheduler.get_stats()['version']}")
        print(f"   Jobs loaded: {scheduler.get_stats()['total_jobs']}")
        print("   Press Ctrl+C to stop...")

        # 保持运行
        try:
            import signal
            import time
            stop_event = threading.Event()

            def handle_signal(signum, frame):
                print("\n🛑 Stopping scheduler...")
                scheduler.stop()
                stop_event.set()

            signal.signal(signal.SIGINT, handle_signal)
            signal.signal(signal.SIGTERM, handle_signal)

            while not stop_event.is_set():
                time.sleep(1)

        except KeyboardInterrupt:
            scheduler.stop()
            print("\nScheduler stopped")
    else:
        print("❌ Failed to start scheduler")
        sys.exit(1)


def cmd_stop(args):
    """停止调度器（通过 PID 文件或其他方式）"""
    # 简化版：提示用户通过 Ctrl+C 停止
    # 完整实现需要 PID 文件管理或进程通信
    print("ℹ️  To stop the scheduler, press Ctrl+C in the terminal where it's running.")
    print("   For daemon mode, use launchd/systemd/Windows Service.")


def cmd_status(args):
    """查看调度器状态"""
    from tools.scheduler import get_scheduler
    from tools.scheduler.state_store import get_state_store

    store = get_state_store()
    state = store.get_scheduler_state()

    print("📊 Scheduler Status")
    print("-" * 40)
    print(f"  State:         {state.get('state', 'unknown')}")
    print(f"  Version:       {state.get('version', 'N/A')}")
    print(f"  Node:          {state.get('node_id', 'N/A')}")
    print(f"  Last heartbeat: {state.get('last_heartbeat', 'N/A')}")
    print(f"  Started at:    {state.get('started_at', 'N/A')}")
    print()

    # 统计
    from tools.scheduler.record_writer import get_record_writer
    stats = get_record_writer().get_today_stats()
    print("📈 Today's Stats")
    print("-" * 40)
    print(f"  Total:   {stats['total']}")
    print(f"  Success: {stats['success']}")
    print(f"  Failed:  {stats['failed']}")
    print(f"  DLQ:     {stats['dlq']}")


def cmd_list(args):
    """列出所有任务"""
    from tools.scheduler.registry import task_registry

    tasks = task_registry.list_all()

    if not tasks:
        print("No tasks registered.")
        return

    print(f"📋 Registered Tasks ({len(tasks)})")
    print("-" * 60)
    print(f"  {'Name':<30} {'Type':<10} {'Status':<10} {'Retries':<8}")
    print("-" * 60)

    for task in tasks:
        print(f"  {task.name:<30} {task.trigger_type.value:<10} {task.status:<10} {task.max_retries:<8}")

    print("-" * 60)


def cmd_run(args):
    """手动执行任务"""
    from tools.scheduler import get_scheduler

    scheduler = get_scheduler()
    result = scheduler.run_task_now(args.task_name)

    if result is None:
        print(f"❌ Task not found: {args.task_name}")
        sys.exit(1)

    if result.success:
        print(f"✅ Task executed successfully: {args.task_name}")
        if result.message:
            print(f"   Message: {result.message}")
        if result.data and args.verbose:
            print(f"   Data: {json.dumps(result.data, ensure_ascii=False, indent=2)}")
    else:
        print(f"❌ Task failed: {args.task_name}")
        print(f"   Error: {result.message}")
        print(f"   Code:  {result.error_code}")
        sys.exit(1)


def cmd_show(args):
    """查看任务详情"""
    from tools.scheduler.registry import task_registry
    from tools.scheduler import get_scheduler

    task = task_registry.get(args.task_name)
    if not task:
        print(f"❌ Task not found: {args.task_name}")
        sys.exit(1)

    print(f"🔍 Task Details: {task.name}")
    print("-" * 40)
    print(f"  Description:  {task.description or '-'}")
    print(f"  Function:     {task.func_ref}")
    print(f"  Trigger type: {task.trigger_type.value}")
    print(f"  Trigger:      {task.trigger_config}")
    print(f"  Status:       {task.status}")
    print(f"  Max retries:  {task.max_retries}")
    print(f"  Timeout:      {task.timeout}s")
    print(f"  Idempotency:  {task.idempotency_key_expr}")
    print(f"  Version:      {task.version}")

    # 调度信息
    scheduler = get_scheduler()
    if scheduler.is_running():
        info = scheduler.get_job_info(args.task_name)
        if info:
            print(f"  Next run:     {info['next_run_time']}")

    print()


def cmd_pause(args):
    """暂停任务"""
    from tools.scheduler import get_scheduler

    scheduler = get_scheduler()
    if not scheduler.is_running():
        # 未运行时只更新数据库状态
        from tools.scheduler.state_store import get_state_store
        get_state_store().update_job_status(args.task_name, "paused")
        print(f"⏸️  Task paused (database only): {args.task_name}")
        return

    if scheduler.pause_task(args.task_name):
        print(f"⏸️  Task paused: {args.task_name}")
    else:
        print(f"❌ Failed to pause task: {args.task_name}")
        sys.exit(1)


def cmd_resume(args):
    """恢复任务"""
    from tools.scheduler import get_scheduler

    scheduler = get_scheduler()
    if not scheduler.is_running():
        from tools.scheduler.state_store import get_state_store
        get_state_store().update_job_status(args.task_name, "active")
        print(f"▶️  Task resumed (database only): {args.task_name}")
        return

    if scheduler.resume_task(args.task_name):
        print(f"▶️  Task resumed: {args.task_name}")
    else:
        print(f"❌ Failed to resume task: {args.task_name}")
        sys.exit(1)


def cmd_history(args):
    """查看执行历史"""
    from tools.scheduler.state_store import get_state_store

    store = get_state_store()
    records = store.list_executions(
        task_name=args.task_name,
        status=args.status,
        limit=args.limit,
    )

    if not records:
        print("No execution records found.")
        return

    print(f"📜 Execution History ({len(records)})")
    print("-" * 80)
    print(f"  {'Time':<20} {'Task':<25} {'Status':<10} {'Duration':<10} {'Retry'}")
    print("-" * 80)

    for rec in records:
        time_str = rec.start_time.strftime("%Y-%m-%d %H:%M:%S") if rec.start_time else "-"
        dur_str = f"{rec.duration:.1f}s" if rec.duration else "-"
        print(f"  {time_str:<20} {rec.task_name:<25} {rec.status:<10} {dur_str:<10} {rec.retry_count}")

    print("-" * 80)


def cmd_stats(args):
    """查看统计信息"""
    from tools.scheduler import get_scheduler

    scheduler = get_scheduler()
    stats = scheduler.get_stats()

    print("📊 Scheduler Statistics")
    print("-" * 40)
    print(f"  Running:        {stats['running']}")
    print(f"  Total jobs:     {stats['total_jobs']}")
    print(f"  Active jobs:    {stats['active_jobs']}")
    print(f"  Today total:    {stats['today_total']}")
    print(f"  Today success:  {stats['today_success']}")
    print(f"  Today failed:   {stats['today_failed']}")
    print(f"  Today DLQ:      {stats['today_dlq']}")
    print(f"  Node:           {stats['node_id']}")
    print(f"  Version:        {stats['version']}")


def cmd_dlq(args):
    """查看死信队列"""
    from tools.scheduler.state_store import get_state_store

    store = get_state_store()
    records = store.list_executions(status="dlq", limit=args.limit)

    if not records:
        print("✅ No tasks in dead letter queue.")
        return

    print(f"💀 Dead Letter Queue ({len(records)})")
    print("-" * 80)
    print(f"  {'Time':<20} {'Task':<25} {'Retries':<8} {'Error'}")
    print("-" * 80)

    for rec in records:
        time_str = rec.end_time.strftime("%Y-%m-%d %H:%M:%S") if rec.end_time else "-"
        err = (rec.error_message or "")[:40]
        print(f"  {time_str:<20} {rec.task_name:<25} {rec.retry_count:<8} {err}")

    print("-" * 80)
    print(f"\n  Total in DLQ: {len(records)}")


def main():
    parser = argparse.ArgumentParser(
        description="定时任务系统 - DevProjectTeamSkill",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s start --tasks-dir tools/scheduler/tasks
  %(prog)s status
  %(prog)s list
  %(prog)s run my_task
  %(prog)s history --task-name my_task --limit 10
  %(prog)s stats
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # start
    p_start = subparsers.add_parser("start", help="Start scheduler")
    p_start.add_argument("--config", help="Config file path (YAML)")
    p_start.add_argument("--tasks-dir", help="Tasks directory to load")
    p_start.add_argument("--tasks-yaml", help="Tasks YAML config file")

    # stop
    subparsers.add_parser("stop", help="Stop scheduler")

    # status
    subparsers.add_parser("status", help="Show scheduler status")

    # list
    subparsers.add_parser("list", help="List all tasks")

    # run
    p_run = subparsers.add_parser("run", help="Run task manually")
    p_run.add_argument("task_name", help="Task name")
    p_run.add_argument("-v", "--verbose", action="store_true", help="Show detailed output")

    # show
    p_show = subparsers.add_parser("show", help="Show task details")
    p_show.add_argument("task_name", help="Task name")

    # pause
    p_pause = subparsers.add_parser("pause", help="Pause task")
    p_pause.add_argument("task_name", help="Task name")

    # resume
    p_resume = subparsers.add_parser("resume", help="Resume task")
    p_resume.add_argument("task_name", help="Task name")

    # history
    p_hist = subparsers.add_parser("history", help="Show execution history")
    p_hist.add_argument("--task-name", help="Filter by task name")
    p_hist.add_argument("--status", help="Filter by status")
    p_hist.add_argument("--limit", type=int, default=20, help="Max records (default: 20)")

    # stats
    subparsers.add_parser("stats", help="Show statistics")

    # dlq
    p_dlq = subparsers.add_parser("dlq", help="Show dead letter queue")
    p_dlq.add_argument("--limit", type=int, default=20, help="Max records (default: 20)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    # 分发命令
    commands = {
        "start": cmd_start,
        "stop": cmd_stop,
        "status": cmd_status,
        "list": cmd_list,
        "run": cmd_run,
        "show": cmd_show,
        "pause": cmd_pause,
        "resume": cmd_resume,
        "history": cmd_history,
        "stats": cmd_stats,
        "dlq": cmd_dlq,
    }

    func = commands.get(args.command)
    if func:
        func(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    import threading  # 提前导入，供 start 命令使用
    main()
