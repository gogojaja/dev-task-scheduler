# 定时任务系统 (tools/scheduler)

> 轻量、可靠、可观测的本地定时任务调度框架
> 基于 APScheduler 封装，提供企业级特性

---

## 特性

- ✅ **可靠调度**：基于 APScheduler，支持 cron/interval/date 三种触发器
- ✅ **幂等保障**：框架层强制幂等校验，重复执行零副作用
- ✅ **重试策略**：指数退避 + 抖动 + 死信队列，避免重试风暴
- ✅ **状态持久化**：SQLite 存储任务状态，进程崩溃可恢复
- ✅ **执行记录**：CSV 台账格式，审计可追溯
- ✅ **告警通知**：系统通知 + Webhook，失败/堆积/心跳异常告警
- ✅ **超时控制**：任务超时强制终止，防止单个任务阻塞
- ✅ **跨平台**：macOS / Linux / Windows 全平台支持
- ✅ **多种注册方式**：装饰器 / YAML 配置 / 编程式注册
- ✅ **TRAE 集成**：可与 TRAE Schedule 工具配合使用

---

## 快速开始

### 1. 安装依赖

```bash
pip install apscheduler pyyaml
```

### 2. 编写任务

```python
# tools/scheduler/tasks/my_tasks.py
from tools.scheduler import register_task

@register_task(
    name="daily_backup",
    trigger="cron",
    hour=2,
    minute=0,
    description="每日备份任务",
    idempotency_key="{date}",
)
def daily_backup():
    # 你的任务逻辑
    return "Backup completed"
```

### 3. 启动调度器

```bash
# 前台运行
python tools/scheduler/cli.py start --tasks-dir tools/scheduler/tasks

# 或使用配置文件
python tools/scheduler/cli.py start --config tools/scheduler/templates/scheduler.example.yaml
```

### 4. 管理任务

```bash
# 查看状态
python tools/scheduler/cli.py status

# 列出任务
python tools/scheduler/cli.py list

# 手动执行
python tools/scheduler/cli.py run daily_backup

# 查看执行历史
python tools/scheduler/cli.py history --task-name daily_backup

# 查看统计
python tools/scheduler/cli.py stats

# 查看死信队列
python tools/scheduler/cli.py dlq
```

---

## 任务注册方式

### 方式一：装饰器（推荐）

```python
from tools.scheduler import register_task

@register_task(
    name="my_task",
    trigger="cron",
    hour=2,
    minute=0,
    description="我的任务",
    idempotency_key="{date}",
    max_retries=3,
    timeout=300,
)
def my_task():
    return "success"
```

### 方式二：YAML 配置

```yaml
tasks:
  - name: my_task
    description: 我的任务
    trigger:
      type: cron
      cron: "0 2 * * *"
    module: tasks.my_module
    function: my_task
    idempotency_key: "{date}"
    max_retries: 3
    timeout: 300
```

### 方式三：编程式

```python
from tools.scheduler import get_scheduler
from tools.scheduler.models import TaskDefinition, TriggerType

task_def = TaskDefinition(
    name="my_task",
    func_ref="tasks.my_module:my_task",
    trigger_type=TriggerType.CRON,
    trigger_config={"hour": 2, "minute": 0},
)

scheduler = get_scheduler()
scheduler.add_task(task_def)
```

---

## 幂等键表达式

支持以下变量替换：

| 变量 | 说明 | 示例 |
|------|------|------|
| `{date}` | 日期 | `2026-08-21` |
| `{datetime}` | 按小时的日期时间 | `2026-08-21 14:00:00` |
| `{task_name}` | 任务名称 | `my_task` |
| `{run_id}` | 运行 ID | `uuid-xxx` |
| `{weekday}` | 星期几（0=周一） | `0` |
| `{month}` | 月份 | `08` |
| `{year}` | 年份 | `2026` |
| `{param:xxx}` | 任务参数 | 从 params 中取值 |

完整幂等键格式：`{task_name}:{表达式结果}`

---

## 触发器类型

### Cron 触发器

```python
@register_task(
    name="daily_task",
    trigger="cron",
    hour=2,
    minute=0,
)
```

支持的参数：`year`, `month`, `day`, `week`, `day_of_week`, `hour`, `minute`, `second`

Cron 表达式格式：`分 时 日 月 周`

### Interval 触发器

```python
@register_task(
    name="interval_task",
    trigger="interval",
    minutes=30,
)
```

支持的参数：`weeks`, `days`, `hours`, `minutes`, `seconds`

### Date 触发器

```python
@register_task(
    name="one_time_task",
    trigger="date",
    run_date="2026-12-31 23:59:59",
)
```

---

## 任务返回值

任务函数可以返回多种类型：

| 返回类型 | 说明 |
|---------|------|
| `None` / 无返回 | 视为成功 |
| `True` | 视为成功 |
| `str` | 视为成功，字符串作为 message |
| `dict` | 视为成功，dict 作为 result.data |
| `TaskResult` | 完整的结果对象 |
| 抛出异常 | 视为失败，进入重试流程 |

### 使用 TaskResult 控制行为

```python
from tools.scheduler.models import TaskResult

def my_task():
    if some_business_error:
        # 业务错误，不重试，直接进死信
        return TaskResult.fail(
            message="Invalid data",
            skip_retry=True,  # 关键：不重试
        )

    if some_temporary_error:
        # 临时错误，可以重试
        return TaskResult.fail(
            message="Service unavailable",
            skip_retry=False,  # 可以重试（默认）
        )

    return TaskResult.ok(message="Done", data={"count": 42})
```

---

## 目录结构

```
tools/scheduler/
├── __init__.py           # 包入口，导出公共 API
├── cli.py                # 命令行接口
├── config.py             # 配置管理
├── models.py             # 数据模型
├── utils.py              # 工具函数
├── state_store.py        # SQLite 状态存储
├── record_writer.py      # CSV 执行记录
├── idempotency.py        # 幂等校验
├── retry.py              # 重试策略
├── executor.py           # 执行框架
├── registry.py           # 任务注册
├── notifier.py           # 告警通知
├── scheduler.py          # 调度引擎管理
├── tasks/                # 内置任务目录
│   ├── __init__.py
│   └── example_tasks.py  # 示例任务
└── templates/            # 配置模板
    ├── scheduler.example.yaml
    └── tasks.example.yaml
```

---

## 存储位置

| 数据 | 路径 | 说明 |
|------|------|------|
| SQLite 数据库 | `.secrets/scheduler.db` | gitignore，不提交 |
| 执行记录 | `台账/31_定时任务执行记录.csv` | UTF-8 BOM，纳入版本控制 |
| 日志 | `logs/scheduler/` | gitignore |

---

## 与 TRAE Schedule 集成

### 架构关系

```
TRAE Schedule（用户入口 / cron 触发）
    ↓
APScheduler（执行引擎 / 可靠调度）
    ↓
任务脚本 / 业务逻辑
```

### 使用方式

1. 通过 TRAE Schedule 创建定时任务
2. TRAE Schedule 的 cron 触发调用 APScheduler 执行
3. APScheduler 负责幂等、重试、死信、记录等可靠性保障

---

## 进程守护

### macOS (launchd)

创建 `~/Library/LaunchAgents/com.devproject.scheduler.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.devproject.scheduler</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/path/to/tools/scheduler/cli.py</string>
        <string>start</string>
        <string>--tasks-dir</string>
        <string>/path/to/tools/scheduler/tasks</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/path/to/logs/scheduler/stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/path/to/logs/scheduler/stderr.log</string>
</dict>
</plist>
```

```bash
# 加载
launchctl load ~/Library/LaunchAgents/com.devproject.scheduler.plist

# 卸载
launchctl unload ~/Library/LaunchAgents/com.devproject.scheduler.plist
```

### Linux (systemd)

创建 `/etc/systemd/system/scheduler.service`：

```ini
[Unit]
Description=DevProjectTeamSkill Scheduler
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/project
ExecStart=/usr/bin/python3 tools/scheduler/cli.py start --tasks-dir tools/scheduler/tasks
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
# 启用并启动
sudo systemctl enable scheduler
sudo systemctl start scheduler

# 查看状态
sudo systemctl status scheduler

# 查看日志
journalctl -u scheduler -f
```

### Windows (服务)

使用 NSSM 或 Task Scheduler 将 CLI 注册为服务。

---

## 配置说明

### scheduler（调度器）

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| timezone | Asia/Shanghai | 调度时区 |
| jobstore.path | .secrets/scheduler.db | SQLite 数据库路径 |
| executor.max_workers | 10 | 最大并发 Worker 数 |
| misfire_grace_time | 3600 | 错过触发宽限时间（秒） |
| coalesce | true | 多次 misfire 是否合并执行 |

### execution（执行）

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| default_max_retries | 3 | 默认最大重试次数 |
| default_timeout | 300 | 默认超时时间（秒） |
| retry_base_delay | 60 | 初始重试延迟（秒） |
| retry_max_delay | 3600 | 最大重试延迟（秒） |
| retry_jitter | 30 | 重试抖动范围（秒） |

### recording（记录）

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| enabled | true | 是否启用 CSV 记录 |
| csv_path | 台账/31_定时任务执行记录.csv | CSV 文件路径 |
| archive_days | 90 | SQLite 记录保留天数 |

### alerting（告警）

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| enabled | true | 是否启用告警 |
| system_notification | true | 是否启用系统通知 |
| failed_alert_threshold | 1 | 失败几次后告警 |
| queue_alert_threshold | 50 | 队列堆积阈值 |
| heartbeat_timeout | 300 | 心跳超时（秒） |
| webhook_url | "" | Webhook 地址（可选） |

---

## 错误码

格式：`SCH-XX-YYY`

| 错误码 | 说明 | 级别 |
|--------|------|------|
| SCH-00-000 | 执行成功 | - |
| SCH-01-001 | 配置文件不存在 | 错误 |
| SCH-02-001 | 任务名称重复 | 错误 |
| SCH-03-001 | 调度器启动失败 | 严重 |
| SCH-04-001 | 任务执行异常 | 错误 |
| SCH-04-002 | 任务执行超时 | 警告 |
| SCH-05-001 | 幂等键冲突 | 警告 |
| SCH-06-001 | 数据库连接失败 | 严重 |
| SCH-09-001 | 权限不足 | 错误 |

---

## 架构设计

详见 [架构资产文档](../../../架构资产/)。

---

## 版本

- v1.0.0 — 初始版本
