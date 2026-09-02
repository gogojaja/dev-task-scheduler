# 架构决策记录（ADR）

> **项目编号**：PRJ-TS-001  
> **项目名称**：dev-task-scheduler  
> **文档版本**：v1.0  
> **创建日期**：2026-09-02  
> **决策基线**：SRS v1.0（需求基线已冻结）  
> **决策状态**：已评审通过

---

## ADR-001：调度内核选型 — APScheduler 3.11.3

### 状态
已接受（Accepted）

### 上下文
项目需要一个可靠的本地定时任务调度内核，支持 cron/interval/date 三种触发器，具备 misfire 处理、线程池执行、JobStore 持久化等能力。候选方案：

| 方案 | 优点 | 缺点 |
|-----|------|------|
| APScheduler 3.11.3 | 成熟稳定/三触发器/WAL JobStore/线程进程异步/PyPI 发布 | 分布式能力弱（本项目不需要） |
| Celery + Beat | 分布式/高可用 | 依赖 Redis/RabbitMQ，零预算不符 |
| schedule (轻量库) | 极简 | 无持久化/无 misfire/无重试/功能不足 |
| 自研调度内核 | 完全可控 | 开发成本极高/可靠性难保证/迭代周期不允许 |

### 决策
选择 **APScheduler 3.11.3** 作为调度内核，通过 `BackgroundScheduler` + `SqliteJobStore`（WAL 模式）封装为 `SchedulerManager` 单例。

### 理由
1. APScheduler 是 Python 生态最成熟的本地调度框架，API 稳定
2. 原生支持 cron/interval/date 三种触发器，完全覆盖 SRS 需求
3. 内置线程池执行器，满足 100+ 并发任务性能需求
4. 零外部基础设施依赖（无 Redis/MQ），符合零预算约束
5. 通过抽象层 `SchedulerManager` 封装，保留内核可替换性（DC-010 质量属性）

### 后果
- 正面：开发效率高，API 成熟，文档完善
- 负面：APScheduler 版本升级需评估兼容性（R-001 风险）
- 缓解：通过 `SchedulerManager` 抽象层隔离，内核替换仅需修改该模块

### 追溯
REQ-FUNC-SCH-001/002/003, DC-001

---

## ADR-002：状态持久化 — SQLite WAL 模式

### 状态
已接受（Accepted）

### 上下文
需要持久化任务定义、执行状态、幂等键、调度器状态等热数据。要求崩溃恢复、低延迟写入、零外部依赖。候选方案：

| 方案 | 优点 | 缺点 |
|-----|------|------|
| SQLite WAL | 零依赖/单文件/WAL 并发/崩溃恢复/跨平台 | 写入吞吐有限（本项目足够） |
| PostgreSQL/MySQL | 高吞吐/分布式 | 需外部服务/零预算不符 |
| Redis | 高速缓存 | 需外部服务/持久化弱 |
| JSON 文件 | 极简 | 无事务/并发不安全/无索引 |

### 决策
选择 **SQLite WAL 模式** 作为唯一持久化存储，数据库文件默认位于 `.secrets/scheduler.db`。

### 理由
1. 零外部依赖，符合零预算约束
2. WAL 模式支持并发读，写入性能满足 <10ms 需求
3. 内建事务和崩溃恢复，满足崩溃恢复 <5s 需求
4. 单文件便于备份和迁移
5. Python 标准库内置 sqlite3，无额外依赖

### 数据库 Schema 设计
四表结构：
- `jobs`：任务定义（name UNIQUE, func_ref, trigger_type/config, max_retries, timeout 等）
- `job_executions`：执行记录（run_id UNIQUE, status, duration, error_code 等）
- `idempotency_keys`：幂等键（idempotency_key UNIQUE, status, result_data）
- `scheduler_state`：调度器单例状态（state, heartbeat, version）

### 后果
- 正面：零依赖、崩溃恢复、低延迟
- 负面：不支持多进程并发写入（本项目单进程设计，无影响）
- 缓解：通过 `threading.Lock` 保证线程安全

### 追溯
REQ-FUNC-STS-001, DC-001

---

## ADR-003：幂等校验 — 表达式驱动的键值去重

### 状态
已接受（Accepted）

### 上下文
框架层需强制幂等校验，确保任务重复执行零副作用。需支持灵活的幂等键表达式，覆盖按日/按小时/按参数等多种去重场景。

### 决策
采用 **表达式驱动的幂等键生成机制**：

- 幂等键格式：`{task_name}:{表达式结果}`
- 支持 8 种变量替换：`{date}`, `{datetime}`, `{task_name}`, `{run_id}`, `{weekday}`, `{month}`, `{year}`, `{param:xxx}`
- 存储于 SQLite `idempotency_keys` 表，UNIQUE 约束保证原子性
- 已成功执行的幂等键再次匹配时返回 SKIPPED，不进入重试队列

### 理由
1. 表达式模式灵活，用户通过简单字符串即可定义去重规则
2. SQLite UNIQUE 约束保证并发场景下的幂等原子性
3. 与执行记录解耦，幂等键独立存储和查询

### 后果
- 正面：使用简单（3 行代码注册即享幂等保护）
- 负面：幂等键表会持续增长，需定期清理
- 缓解：结合 `cleanup_old_executions` 方法定期清理过期幂等键

### 追溯
REQ-FUNC-IDM-001

---

## ADR-004：重试策略 — 指数退避 + 抖动 + 死信队列

### 状态
已接受（Accepted）

### 上下文
任务执行失败时需重试，但要避免重试风暴。需区分临时错误（可重试）和业务错误（不可重试）。

### 决策
采用 **指数退避 + 随机抖动 + 最大重试次数 + 死信队列（DLQ）** 策略：

- 退避公式：`delay = min(base_delay × factor^retry_count, max_delay) + random(0, jitter)`
- 默认参数：max_retries=3, base_delay=60s, max_delay=3600s, jitter=30s, factor=2.0
- `TaskResult.fail(skip_retry=True)` 业务错误直接进入 DLQ
- `TaskResult.fail(skip_retry=False)` 按退避策略重试
- 达到最大重试次数后进入 DLQ

### 理由
1. 指数退避避免重试风暴
2. 随机抖动进一步降低并发重试碰撞概率
3. 死信队列隔离不可恢复错误，避免无限重试
4. `skip_retry` 标志支持业务层精确控制

### 后果
- 正面：重试策略可配置、可控制、可观测
- 负面：重试延迟较长（首次 60s），对实时性要求高的场景不适用
- 缓解：每个任务可独立配置重试参数

### 追溯
REQ-FUNC-RTY-001

---

## ADR-005：超时控制 — 线程 join + daemon 模式

### 状态
已接受（Accepted）

### 上下文
单个任务执行可能阻塞调度器，需要超时强制终止机制。需跨平台兼容 macOS/Linux/Windows。

### 决策
采用 **线程 join(timeout) + daemon 线程** 模式实现超时控制：

- 任务在 daemon 线程中执行，主线程 `thread.join(timeout=task.timeout)`
- 超时后主线程继续，daemon 线程随进程退出自动清理
- 默认超时 300 秒，每个任务可独立配置
- 超时结果标记为 `TASK_TIMEOUT(SCH-04-002)`，状态设为 FAILED

### 候选方案对比
| 方案 | 跨平台 | 可靠性 | 复杂度 |
|-----|--------|--------|--------|
| 线程 join + daemon | ✅ 三平台 | 中（线程无法强制 kill） | 低 |
| signal.SIGALRM | ❌ 仅 Unix | 高 | 中 |
| multiprocessing + terminate | ✅ 但重 | 高 | 高 |
| subprocess + kill | ✅ 但重 | 高 | 高 |

### 理由
1. 三平台完全兼容，符合 DC-002 约束
2. 实现简洁，无平台特定代码
3. daemon 线程在进程退出时自动清理，无资源泄漏
4. 对于 CPU 密集型任务，配合 GIL 释放可正常响应超时

### 后果
- 正面：跨平台、低复杂度、无资源泄漏
- 负面：对于阻塞 I/O 的线程，无法立即强制终止
- 缓解：文档说明超时行为，建议任务函数内部自行检查超时

### 追溯
REQ-FUNC-TMO-001, DC-002

---

## ADR-006：执行记录 — SQLite + CSV 双写架构

### 状态
已接受（Accepted）

### 上下文
每次任务执行需产生可审计的记录。需同时满足：
1. 程序内查询需求（SQLite 快速检索）
2. 人工审计需求（CSV 台账可读性）

### 决策
采用 **SQLite + CSV 双写架构**：

- SQLite `job_executions` 表：程序内查询、统计、历史检索
- CSV 台账 `台账/31_定时任务执行记录.csv`：人工审计、外部工具导入
- CSV 格式：UTF-8 with BOM，15 列标准字段
- 追加写入模式，不覆盖历史记录
- 两写入点独立，任一失败不阻塞另一

### 理由
1. CSV 台账与项目治理体系统一（所有台账均为 CSV 格式，DC-009）
2. SQLite 提供高效的程序化查询能力
3. 双写互不依赖，提高可靠性

### 后果
- 正面：兼顾程序查询和人工审计
- 负面：双写有少量性能开销
- 缓解：CSV 追加 <5ms，SQLite 写入 <10ms，总开销可接受

### 追溯
REQ-FUNC-REC-001, DC-009

---

## ADR-007：告警通知 — 系统通知 + Webhook 双渠道

### 状态
已接受（Accepted）

### 上下文
任务失败、堆积、心跳丢失等异常需及时告警。需支持本地通知和远程通知两种方式。

### 决策
采用 **系统通知 + Webhook 双渠道告警**：

- 系统通知：macOS(osascript) / Linux(notify-send) / Windows(PowerShell Toast)
- Webhook：HTTP POST JSON payload，超时 30 秒
- 告警频率控制：同类告警最小间隔 5 分钟，防止告警风暴
- 告警失败不阻塞主流程（SCH-08-001 降级策略）
- 四类告警：任务失败 / 任务 DLQ / 队列堆积 / 心跳丢失

### 理由
1. 系统通知零配置即用，适合个人开发场景
2. Webhook 支持集成企业告警平台（钉钉/飞书/Slack 等）
3. 频率控制避免告警风暴
4. 降级策略保证告警故障不影响核心调度

### 后果
- 正面：零配置即可使用，可扩展
- 负面：系统通知依赖桌面环境，headless 服务器无效
- 缓解：headless 场景使用 Webhook 渠道

### 追溯
REQ-FUNC-ALT-001, REQ-FUNC-ALT-002

---

## ADR-008：模块架构 — 五层管道单向数据流

### 状态
已接受（Accepted）

### 上下文
框架需模块化设计，各层职责清晰、可独立测试、可替换。

### 决策
采用 **五层管道单向数据流** 架构：

```
注册层(Registry) → 调度层(Scheduler) → 执行层(Executor) → 存储层(StateStore) → 记录层(RecordWriter)
     ↑                    ↑                   ↑                  ↑                    ↑
  任务定义           APScheduler封装      幂等/重试/超时      SQLite WAL          CSV 台账
```

各层通过全局单例访问，依赖方向单向向下：
- `SchedulerManager` 依赖 `Registry`, `Executor`, `StateStore`, `Notifier`, `RetryPolicy`
- `TaskExecutor` 依赖 `StateStore`, `RecordWriter`, `IdempotencyManager`, `RetryPolicy`
- 底层模块（StateStore, RecordWriter, Notifier）不依赖上层

### 理由
1. 单向依赖，模块间低耦合
2. 每层可独立替换（如替换 StateStore 为 Redis 实现）
3. 全局单例简化使用，适合单进程场景
4. 与 APScheduler 的 BackgroundScheduler 模型匹配

### 后果
- 正面：模块化、可测试、可替换
- 负面：全局单例在测试时需要手动重置
- 缓解：提供 `set_config()` 等注入接口，支持测试时替换

### 追溯
质量属性-维护性-模块化

---

## ADR-009：配置管理 — YAML 四域配置 + 环境变量覆盖

### 状态
已接受（Accepted）

### 上下文
需提供灵活的配置机制，覆盖调度器/执行/记录/告警四大配置域。

### 决策
采用 **YAML 配置文件 + 编程式默认值** 的配置管理：

- 四大配置域：scheduler / execution / recording / alerting
- YAML 文件可选，缺失时使用编程式默认值
- 路径解析：相对路径基于项目根目录
- 配置通过 `AppConfig` dataclass 强类型管理
- 跨项目通过 `PROJECT_ROOT` 环境变量切换配置基准路径

### 理由
1. YAML 可读性好，适合运维人员修改
2. 编程式默认值保证零配置即可启动
3. dataclass 提供类型安全和 IDE 自动补全
4. PROJECT_ROOT 支持跨项目配置隔离

### 后果
- 正面：零配置启动 + 灵活定制
- 负面：不支持运行时热更新配置
- 缓解：配置变更需重启调度器，文档明确说明

### 追溯
REQ-FUNC-CFG-001, REQ-FUNC-PRJ-001

---

## ADR-010：任务注册 — 三模式统一注册

### 状态
已接受（Accepted）

### 上下文
需支持装饰器、YAML 配置、编程式三种任务注册方式，降低使用门槛。

### 决策
采用 **统一 `TaskRegistry` + 三入口** 的注册模式：

1. **装饰器注册**：`@register_task(name, trigger, ...)` — 最简 3 行代码
2. **YAML 配置注册**：`registry.load_from_yaml(path)` — 批量注册
3. **编程式注册**：`registry.register(TaskDefinition(...))` — 完全控制

三种方式最终都通过 `TaskRegistry.register()` 统一注册到内存 + SQLite。

### 理由
1. 装饰器模式降低入门门槛（3 行代码）
2. YAML 模式适合运维场景（无需改代码）
3. 编程式适合复杂场景（动态注册）
4. 统一入口保证行为一致

### 后果
- 正面：多种使用方式，适应不同场景
- 负面：同名任务重复注册行为需明确（当前策略：覆盖 + 警告日志）
- 缓解：文档说明重复注册行为

### 追溯
REQ-FUNC-REG-001/002/003

---

## ADR-011：跨平台兼容 — 纯 Python + 条件分支

### 状态
已接受（Accepted）

### 上下文
需同时支持 macOS/Linux/Windows 三平台，行尾 LF，禁止 Windows 专属命令。

### 决策
采用 **纯 Python 标准库 + 平台条件分支** 策略：

- 核心逻辑全用 Python 标准库，无平台特定依赖
- 平台差异点（系统通知、信号处理）通过 `platform.system()` 条件分支
- 超时控制使用线程 join 而非 Unix signal（跨平台）
- 文件路径使用 `pathlib.Path`（自动处理路径分隔符）
- CSV 编码统一 UTF-8 with BOM（三平台兼容）

### 理由
1. 纯 Python 保证三平台一致性
2. pathlib 自动处理路径差异
3. 线程超时避免 Unix/Windows 信号差异
4. UTF-8 BOM 保证 Windows Excel 正确识别中文

### 后果
- 正面：三平台行为一致，无平台特定 bug
- 负面：无法使用平台特定高级特性
- 缓解：本项目功能不需要平台特定特性

### 追溯
DC-002

---

## ADR-012：CLI 设计 — argparse 标准库 + 子命令模式

### 状态
已接受（Accepted）

### 上下文
需提供 CLI 工具管理调度器和任务。需支持 7+ 命令。

### 决策
采用 **argparse + 子命令** 模式实现 CLI：

- 7 个核心命令：start / status / list / run / history / stats / dlq
- 4 个管理命令：show / pause / resume / stop
- 使用 Python 标准库 argparse，零额外依赖
- 命令分发通过字典映射实现

### 理由
1. argparse 是 Python 标准库，零依赖
2. 子命令模式清晰，易于扩展
3. 自动生成 --help 文档

### 后果
- 正面：零依赖、易扩展、文档自动生成
- 负面：无交互式操作（本项目不需要）
- 缓解：CLI 定位为管理工具，非交互式应用

### 追溯
REQ-FUNC-CLI-001~007

---

## 决策汇总矩阵

| ADR 编号 | 决策主题 | 选型方案 | 替代方案 | 风险等级 | 追溯需求 |
|---------|---------|---------|---------|---------|---------|
| ADR-001 | 调度内核 | APScheduler 3.11.3 | Celery/schedule/自研 | 低 | SCH-001~003 |
| ADR-002 | 状态持久化 | SQLite WAL | PostgreSQL/Redis/JSON | 低 | STS-001 |
| ADR-003 | 幂等校验 | 表达式键值去重 | 分布式锁/版本号 | 低 | IDM-001 |
| ADR-004 | 重试策略 | 指数退避+抖动+DLQ | 固定间隔/无限重试 | 低 | RTY-001 |
| ADR-005 | 超时控制 | 线程 join+daemon | signal/multiprocessing | 中 | TMO-001 |
| ADR-006 | 执行记录 | SQLite+CSV 双写 | 仅 SQLite/仅 CSV | 低 | REC-001 |
| ADR-007 | 告警通知 | 系统通知+Webhook | 邮件/短信 | 低 | ALT-001/002 |
| ADR-008 | 模块架构 | 五层管道单向流 | 微服务/事件驱动 | 低 | 质量属性 |
| ADR-009 | 配置管理 | YAML+dataclass | TOML/环境变量 | 低 | CFG-001 |
| ADR-010 | 任务注册 | 三模式统一注册 | 单模式 | 低 | REG-001~003 |
| ADR-011 | 跨平台 | 纯 Python+条件分支 | 平台适配层 | 低 | DC-002 |
| ADR-012 | CLI 设计 | argparse 子命令 | click/typer | 低 | CLI-001~007 |

---

*文档版本：v1.0*  
*生成日期：2026-09-02*  
*架构基线：v0.3（架构基线）*  
*知识产权所有：段波（验证邮箱：duanbo.douglas@163.com）*
