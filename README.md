# dev-task-scheduler

轻量、可靠、可观测的本地定时任务调度框架。

## 特性

- **核心调度引擎**：基于 APScheduler，支持 cron/interval/date 三种触发器
- **幂等校验**：防止重复执行，支持自定义键表达式
- **重试策略**：指数退避 + 抖动 + 死信队列，可扩展回调
- **超时控制**：三级超时（线程/信号/进程），智能策略选择
- **状态持久化**：SQLite 存储，崩溃自动恢复
- **执行记录**：CSV 审计日志，支持轮转和多条件查询
- **告警通知**：模板化告警 + Webhook + 系统通知
- **CLI 工具**：start/status/list/run/history/stats/cleanup/recover/validate
- **配置管理**：YAML 配置 + 环境变量覆盖（30+ 变量）+ 校验
- **跨项目复用**：PROJECT_ROOT 三级优先级，多项目上下文隔离

## 安装

```bash
pip install dev-task-scheduler
```

## 快速开始

```python
from scheduler import SchedulerApp
from scheduler.config import load_config

# 加载配置
config = load_config("scheduler.yaml")

# 创建调度器
app = SchedulerApp(config)

# 注册任务
@app.task(name="daily_report", trigger="cron", hour=8, minute=0)
def daily_report():
    print("生成日报")

# 启动
app.start()
```

## 配置

```yaml
scheduler:
  max_concurrent: 3
  default_timeout: 300

tasks:
  - name: daily_report
    func_ref: "my_module:daily_report"
    trigger_type: cron
    trigger_args:
      hour: 8
      minute: 0

idempotency:
  enabled: true
  key_ttl: 3600

retry:
  enabled: true
  max_retries: 3
  base_delay: 1.0
  backoff_factor: 2.0

recording:
  enabled: true
  csv_path: "logs/executions.csv"

alerting:
  enabled: true
  webhook_url: "https://hooks.example.com/xxx"
```

## 环境变量

所有配置项支持 `SCHEDULER_` 前缀环境变量覆盖：

```bash
export SCHEDULER_SCHEDULER__MAX_CONCURRENT=5
export SCHEDULER_IDEMPOTENCY__ENABLED=true
export SCHEDULER_RECORDING__CSV_PATH=/var/log/scheduler.csv
```

## CLI 命令

```bash
# 启动调度器
dev-task-scheduler start

# 查看状态
dev-task-scheduler status

# 手动触发任务
dev-task-scheduler run <task_name>

# 查看历史记录
dev-task-scheduler history [--limit 20]

# 查看统计
dev-task-scheduler stats

# 清理过期数据
dev-task-scheduler cleanup

# 崩溃恢复
dev-task-scheduler recover

# 校验配置
dev-task-scheduler validate
```

## 文档

- [API 参考](docs/api.md)
- [架构概览](docs/architecture.md)
- [错误码参考](docs/error_codes.md)
- [使用指南](docs/usage.md)

## 开发

```bash
# 克隆仓库
git clone https://github.com/your-org/dev-task-scheduler.git
cd dev-task-scheduler

# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest --cov=scheduler --cov-fail-under=80

# 代码检查
ruff check .
ruff format --check .
```

## 许可证

MIT License
