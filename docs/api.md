# API 参考文档

> **模块**: `scheduler` | **版本**: 1.0.0 | **最后更新**: 2026-09-02

---

## 核心类

### `SchedulerManager`

调度器管理器，封装 APScheduler。

```python
from scheduler import get_scheduler

scheduler = get_scheduler()
scheduler.start()
```

| 方法 | 参数 | 返回值 | 说明 |
|-----|------|-------|------|
| `start()` | - | None | 启动调度器（含崩溃恢复） |
| `stop(wait=True)` | `wait: bool` | None | 停止调度器 |
| `run_now(task_name, params=None)` | `str, dict` | None | 手动触发任务 |
| `pause_task(task_name)` | `str` | None | 暂停任务 |
| `resume_task(task_name)` | `str` | None | 恢复任务 |
| `list_tasks()` | - | `list[dict]` | 列出所有已注册任务 |
| `get_task(task_name)` | `str` | `dict` | 获取单个任务详情 |
| `get_status()` | - | `dict` | 获取调度器状态 |

---

### `TaskResult`

任务执行结果封装。

```python
from scheduler import TaskResult

# 成功
result = TaskResult.ok(message="Done", data={"count": 42})

# 失败（可重试）
result = TaskResult.fail(message="Network error")

# 失败（不可重试，业务错误）
result = TaskResult.fail(message="Invalid input", skip_retry=True, error_code="BIZ-001")
```

| 字段 | 类型 | 默认值 | 说明 |
|-----|------|-------|------|
| `success` | `bool` | - | 是否成功 |
| `message` | `str` | `""` | 结果消息 |
| `data` | `dict` | `{}` | 结果数据（JSON 可序列化） |
| `skip_retry` | `bool` | `False` | 是否跳过重试 |
| `error_code` | `str` | `"SCH-00-000"` | 错误码 |

---

### `TaskContext`

任务执行上下文，注入到任务函数中。

```python
from scheduler import TaskContext

def my_task(context: TaskContext):
    print(f"Task: {context.task_name}")
    print(f"Run ID: {context.run_id}")
    print(f"Retry count: {context.retry_count}")
    print(f"Params: {context.params}")
```

| 字段 | 类型 | 说明 |
|-----|------|------|
| `task_name` | `str` | 任务名称 |
| `run_id` | `str` | 本次运行 ID |
| `scheduled_time` | `datetime` | 计划执行时间 |
| `start_time` | `datetime` | 实际开始时间 |
| `retry_count` | `int` | 当前重试次数 |
| `idempotency_key` | `str` | 幂等键 |
| `params` | `dict` | 运行参数 |

---

### `TaskStatus`

任务执行状态枚举。

| 值 | 说明 | 是否终态 |
|----|------|---------|
| `PENDING` | 待调度 | 否 |
| `SCHEDULED` | 已调度 | 否 |
| `RUNNING` | 执行中 | 否 |
| `SUCCESS` | 成功 | 是 |
| `FAILED` | 失败 | 是 |
| `RETRYING` | 重试中 | 否 |
| `DLQ` | 死信队列 | 是 |
| `SKIPPED` | 跳过（幂等去重） | 是 |
| `PAUSED` | 已暂停 | 否 |
| `CANCELLED` | 已取消 | 是 |

---

## 注册任务

### 装饰器注册

```python
from scheduler import register_task

@register_task(
    name="my_task",
    trigger="cron",
    hour=2,
    minute=0,
    description="每日凌晨 2 点执行",
    idempotency_key="{task_name}:{date}",
    max_retries=3,
    timeout=300,
)
def my_task():
    return "done"
```

### `@register_task` 参数

| 参数 | 类型 | 默认值 | 说明 |
|-----|------|-------|------|
| `name` | `str` | 必填 | 任务名称（唯一标识） |
| `trigger` | `str` | `"cron"` | 触发器类型：cron/interval/date |
| `description` | `str` | `""` | 任务描述 |
| `idempotency_key` | `str` | `"{date}"` | 幂等键表达式 |
| `max_retries` | `int` | `3` | 最大重试次数 |
| `timeout` | `int` | `300` | 超时时间（秒） |
| `params` | `dict` | `{}` | 默认运行参数 |

Cron 触发器额外参数：`year`, `month`, `day`, `week`, `day_of_week`, `hour`, `minute`, `second`

Interval 触发器额外参数：`weeks`, `days`, `hours`, `minutes`, `seconds`

---

## 配置

### YAML 配置文件

```yaml
scheduler:
  timezone: Asia/Shanghai
  jobstore:
    path: .secrets/scheduler.db
  executor:
    type: threadpool
    max_workers: 10
  misfire_grace_time: 3600
  coalesce: true

execution:
  default_max_retries: 3
  default_timeout: 300
  retry_base_delay: 60
  retry_max_delay: 3600
  retry_jitter: 30
  retry_backoff_factor: 2.0

recording:
  enabled: true
  csv_path: 台账/31_定时任务执行记录.csv
  archive_days: 90

alerting:
  enabled: true
  system_notification: true
  failed_alert_threshold: 1
  queue_alert_threshold: 50
  heartbeat_timeout: 300
  webhook_url: ""
```

### 环境变量覆盖

| 环境变量 | 对应配置 | 示例 |
|---------|---------|------|
| `SCHEDULER_TIMEZONE` | scheduler.timezone | `UTC` |
| `SCHEDULER_MAX_WORKERS` | scheduler.max_workers | `5` |
| `SCHEDULER_TIMEOUT` | execution.default_timeout | `600` |
| `SCHEDULER_RECORDING_ENABLED` | recording.enabled | `false` |
| `SCHEDULER_WEBHOOK_URL` | alerting.webhook_url | `https://...` |
| `SCHEDULER_PROJECT_ROOT` | 项目根目录 | `/path/to/project` |

### 配置校验

```python
from scheduler.config import validate_config, enforce_validate, ConfigValidationError

# 校验（返回错误列表）
errors = validate_config()
if errors:
    print(errors)

# 强制校验（抛异常）
try:
    enforce_validate()
except ConfigValidationError as e:
    print(e.errors)
```

---

## CLI 命令

```bash
# 启动调度器
python3 -m scheduler start

# 查看状态
python3 -m scheduler status

# 列出任务
python3 -m scheduler list

# 手动运行任务
python3 -m scheduler run <task_name>

# 查看任务详情
python3 -m scheduler show <task_name>

# 查看执行历史
python3 -m scheduler history [task_name]

# 查看统计信息
python3 -m scheduler stats

# 查看死信队列
python3 -m scheduler dlq

# 清理过期数据
python3 -m scheduler cleanup

# 崩溃恢复
python3 -m scheduler recover

# 校验配置
python3 -m scheduler validate
```
