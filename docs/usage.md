# 使用指南

> **版本**: 1.0.0 | **最后更新**: 2026-09-02

---

## 快速开始

### 1. 安装

```bash
pip install -r requirements.txt
```

### 2. 编写第一个任务

创建文件 `my_tasks.py`：

```python
from scheduler import register_task, TaskResult

@register_task(
    name="hello",
    trigger="cron",
    minute="*/5",  # 每 5 分钟
    description="问候任务",
)
def hello():
    return "Hello, Scheduler!"
```

### 3. 启动调度器

```bash
python3 -m scheduler start
```

---

## 任务模式

### 模式 1：简单任务（无状态）

```python
@register_task(name="backup", trigger="cron", hour=2)
def backup():
    # 直接返回字符串即可
    return "Backup completed"
```

### 模式 2：带上下文的任务（有状态）

```python
from scheduler import TaskContext, TaskResult

@register_task(name="process", trigger="interval", hours=1)
def process(context: TaskContext):
    # 通过 context 获取运行时信息
    params = context.params
    retry_count = context.retry_count

    # 业务逻辑...
    data = {"processed": 100}

    return TaskResult.ok(data=data)
```

### 模式 3：可失败任务（自动重试）

```python
@register_task(
    name="sync",
    trigger="cron",
    hour=3,
    max_retries=3,      # 最多重试 3 次
    timeout=300,         # 超时 300 秒
)
def sync():
    # 临时错误：直接抛异常，系统自动重试
    response = api_call()
    if response.status_code != 200:
        raise RuntimeError("API error")
    return "Sync completed"
```

### 模式 4：业务错误（不重试）

```python
@register_task(name="validate", trigger="cron", hour=1)
def validate():
    data = load_data()
    if not data:
        # skip_retry=True 表示业务错误，不重试
        return TaskResult.fail(
            message="No data to validate",
            skip_retry=True,
            error_code="BIZ-001",
        )
    return TaskResult.ok(message="Validation passed")
```

### 模式 5：带参数的任务

```python
@register_task(
    name="report",
    trigger="cron",
    hour=8,
    params={"days": 7, "format": "pdf"},  # 默认参数
)
def report(context: TaskContext):
    days = context.params.get("days", 7)
    fmt = context.params.get("format", "pdf")
    # 生成报告...
    return TaskResult.ok(data={"file": f"report.{fmt}"})
```

---

## 幂等键表达式

| 表达式 | 含义 | 适用场景 |
|-------|------|---------|
| `{date}` | 当天日期 | 每天只执行一次的任务 |
| `{task_name}:{date}` | 任务名+日期 | 多个任务各自每天一次 |
| `{run_id}` | 运行 ID | 每次运行都执行（不幂等） |
| `{datetime}` | 完整时间 | 每分钟/小时级别的任务 |

---

## 配置指南

### 方式 1：使用默认配置

```python
from scheduler import get_scheduler
scheduler = get_scheduler()
scheduler.start()
```

### 方式 2：YAML 配置文件

```bash
cp scheduler/templates/scheduler.example.yaml scheduler.yaml
# 编辑 scheduler.yaml
python3 -m scheduler start
```

### 方式 3：环境变量

```bash
export SCHEDULER_TIMEZONE=UTC
export SCHEDULER_MAX_WORKERS=5
export SCHEDULER_RECORDING_ENABLED=false
python3 -m scheduler start
```

### 方式 4：编程式配置

```python
from scheduler.config import AppConfig, SchedulerConfig, set_config
from scheduler import get_scheduler

config = AppConfig(
    scheduler=SchedulerConfig(timezone="UTC", max_workers=5),
)
set_config(config)

scheduler = get_scheduler()
scheduler.start()
```

---

## 跨项目接入

### 方式 1：环境变量

```bash
export SCHEDULER_PROJECT_ROOT=/path/to/my-project
python3 -m scheduler start
```

### 方式 2：编程式

```python
from scheduler.context import SchedulerContext

ctx = SchedulerContext(project_root="/path/to/my-project")
ctx.initialize()
scheduler = ctx.create_scheduler()
scheduler.start()
```

---

## 常见问题

### Q: 任务没有执行？

1. 检查 cron 表达式是否正确
2. 确认调度器已启动：`python3 -m scheduler status`
3. 查看日志输出

### Q: 任务重复执行？

1. 设置 `idempotency_key="{task_name}:{date}"`
2. 检查幂等键表达式是否覆盖了重复的时间窗口

### Q: 任务超时？

1. 增加 `timeout` 参数值
2. 检查任务代码是否有阻塞操作
3. 使用 `python3 -m scheduler history` 查看执行记录

### Q: 如何查看死信队列？

```bash
python3 -m scheduler dlq
```

### Q: 如何手动重试死信任务？

```bash
python3 -m scheduler run <task_name>
```
