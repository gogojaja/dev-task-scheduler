# 架构概览

> **模块**: scheduler | **版本**: 1.0.0 | **最后更新**: 2026-09-02

---

## 系统架构图

```
┌─────────────────────────────────────────────────────────────┐
│                        用户层 (CLI / API)                    │
│  cli.py: start/stop/status/list/run/show/history/stats/dlq │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                     调度层 (Scheduler)                        │
│  scheduler.py: APScheduler 封装 / 崩溃恢复 / 任务管理         │
│  context.py:   项目上下文 / 多项目隔离                        │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                     执行层 (Executor)                         │
│  executor.py: 线程执行 / execute_with_retry / 超时控制        │
│  timeout.py:  线程 / 信号 / 进程 三级超时                     │
│  retry.py:    指数退避 / 抖动 / 死信队列 / 回调               │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                     基础设施层                                 │
│  config.py:      配置管理 (YAML / 环境变量 / 校验)             │
│  state_store.py: SQLite 状态持久化 (WAL / 崩溃恢复)           │
│  registry.py:    任务注册表                                   │
│  idempotency.py: 幂等校验 (键生成 / 安全校验)                  │
│  record_writer.py: CSV 执行记录 (BOM / 轮转 / 查询)           │
│  notifier.py:    告警通知 (模板 / Webhook / 系统通知)          │
│  utils.py:       工具函数 / PROJECT_ROOT                      │
└─────────────────────────────────────────────────────────────┘
```

---

## 模块依赖关系

```
scheduler.py ──→ executor.py ──→ timeout.py
       │              │
       │              ├──→ retry.py
       │              ├──→ idempotency.py
       │              ├──→ record_writer.py
       │              └──→ notifier.py
       │
       ├──→ state_store.py
       ├──→ registry.py
       └──→ config.py ──→ utils.py
```

---

## 数据流

### 任务执行流程

```
1. CLI start / get_scheduler().start()
2. scheduler.py: 加载配置 → 崩溃恢复 → 注册任务 → 启动 APScheduler
3. APScheduler 触发 → executor.py: execute_task()
4. 幂等校验 (idempotency.py)
5. 超时控制 (timeout.py) 包裹执行
6. 执行结果 → 重试判断 (retry.py)
7. 记录写入 (record_writer.py + state_store.py)
8. 失败告警 (notifier.py)
```

### 配置加载流程

```
1. YAML 文件解析
2. 环境变量覆盖 (SCHEDULER_*)
3. 校验 (validate_config)
4. 设置全局配置 (set_config)
```

---

## 数据存储

### SQLite (state_store.py)

| 表名 | 用途 | 关键字段 |
|-----|------|---------|
| `jobs` | 任务定义 | name, func_ref, trigger_type, trigger_config |
| `job_executions` | 执行记录 | job_id, run_id, status, start_time, duration |
| `idempotency_keys` | 幂等键 | key, job_id, run_id, status, created_at |
| `scheduler_state` | 调度器状态 | key, value, updated_at |

### CSV (record_writer.py)

路径：`台账/31_定时任务执行记录.csv`

| 列名 | 说明 |
|-----|------|
| 执行编号 | EX-YYYYMMDD-XXXX |
| 任务名称 | 任务唯一标识 |
| 运行ID | UUID |
| 触发方式 | cron/interval/date/manual/retry |
| 计划时间 | ISO 格式 |
| 开始时间 | ISO 格式 |
| 结束时间 | ISO 格式 |
| 耗时(秒) | 浮点数 |
| 状态 | success/failed/dlq/skipped |
| 重试次数 | 整数 |
| 幂等键 | 字符串 |
| 错误码 | SCH-XX-YYY |
| 错误信息 | 文本 |
| 执行节点 | 主机名 |
| 记录时间 | ISO 格式 |

---

## 关键设计决策

| 决策 | 选择 | 理由 |
|-----|------|------|
| 调度内核 | APScheduler 3.11 | 成熟稳定，支持 cron/interval/date |
| 状态存储 | SQLite WAL | 轻量零配置，WAL 支持并发读 |
| CSV 编码 | UTF-8 with BOM | 兼容 Excel 中文显示 |
| 超时策略 | 线程(默认)/信号/进程 | 分级覆盖，信号仅主线程 |
| 重试策略 | 指数退避+抖动 | 避免重试风暴 |
| 幂等键 | `{date}`/`{task_name}:{date}` | 日粒度去重，防止重复执行 |
