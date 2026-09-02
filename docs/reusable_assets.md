# dev-task-scheduler 复用资产清单

> **项目编号**：PRJ-TS-001
> **版本**：v1.0.0
> **最后更新**：2026-09-02

---

## 1. 可复用模块

### 1.1 调度引擎核心（scheduler/）

| 模块 | 文件 | 复用场景 | 依赖 |
|------|------|---------|------|
| 调度引擎 | scheduler.py | 任何需要定时任务的项目 | APScheduler 3.10+ |
| 任务注册 | registry.py | 装饰器/YAML/编程式三种注册模式 | 无外部依赖 |
| 幂等校验 | idempotency.py | 防止重复执行的场景 | SQLite |
| 重试策略 | retry.py | 指数退避+抖动+DLQ | 无外部依赖 |
| 超时控制 | timeout.py | 线程/信号/进程三级超时 | 无外部依赖 |
| 状态持久化 | state_store.py | 任务状态崩溃恢复 | SQLite |
| 执行记录 | record_writer.py | CSV 审计日志 | 无外部依赖 |
| 告警通知 | notifier.py | Webhook/系统通知 | 无外部依赖 |
| 配置管理 | config.py | YAML+环境变量+校验 | PyYAML |
| 工具函数 | utils.py | 跨平台路径/PROJECT_ROOT | 无外部依赖 |
| 项目上下文 | context.py | 多项目隔离运行 | 无外部依赖 |

### 1.2 测试基础设施（tests/）

| 资产 | 文件 | 复用场景 |
|------|------|---------|
| 测试配置 | conftest.py | 14 个 fixture，覆盖 DB/配置/临时目录/清理 |
| IT-02 功能测试 | test_it02_features.py | 超时/CSV轮转/重试回调/告警模板/跨项目 |
| 集成测试 | test_integration.py | 端到端核心流程验证 |
| 覆盖率补充 | test_coverage_supplement.py | 边界 case 覆盖 |
| 外部接入测试 | test_external_integration.py | 外部项目接入路径验证 |

### 1.3 文档体系（docs/）

| 文档 | 文件 | 用途 |
|------|------|------|
| API 参考 | docs/api.md | 全模块 API 文档 |
| 架构概览 | docs/architecture.md | 系统架构+设计模式 |
| 错误码参考 | docs/error_codes.md | 全错误码+处理建议 |
| 使用指南 | docs/usage.md | 快速上手+最佳实践 |
| 复用资产 | docs/reusable_assets.md | 本文件 |

### 1.4 CI/CD 模板（.github/）

| 资产 | 文件 | 复用场景 |
|------|------|---------|
| CI 流水线 | .github/workflows/ci.yml | lint+test(矩阵)+build+security |
| 发布流水线 | .github/workflows/release.yml | tag 触发 PyPI 发布+GitHub Release |

---

## 2. 设计模式复用

### 2.1 五层管道架构
```
Registry → Scheduler → Executor → StateStore → RecordWriter
```
- 单向数据流，模块可独立测试
- 适用于：任何需要"注册→调度→执行→持久化→记录"的系统

### 2.2 幂等键表达式
- `{date}` `{task_name}` `{run_id}` `{random}` 等 8 种变量
- UNIQUE 约束 + TTL 过期清理
- 适用于：任何需要防止重复执行的场景

### 2.3 三级超时策略
- 线程超时（跨平台）→ 信号超时（Unix）→ 进程超时（最强隔离）
- 智能策略自动选择
- 适用于：任何需要任务超时控制的场景

### 2.4 配置三级优先级
- 编程式 `set_project_root()` > 环境变量 `SCHEDULER_PROJECT_ROOT` > 默认
- 适用于：多项目共享同一框架的场景

---

## 3. 经验教训

### 3.1 测试隔离
- **问题**：全局配置单例被测试污染
- **方案**：`autouse=True` fixture 保存/恢复原始配置
- **复用**：所有使用全局单例的项目都应采用此模式

### 3.2 API 签名一致性
- **问题**：测试代码中的 API 调用与实际实现不匹配
- **方案**：迭代开始前先读代码确认 API，再写测试
- **复用**：任何项目迭代开发时的通用实践

### 3.3 覆盖率门禁
- **问题**：非核心模块（tasks/cli/scheduler.py）拉低覆盖率
- **方案**：pyproject.toml 配置 omit + fail_under=80
- **复用**：有入口脚本/示例代码的项目都应排除这些模块

### 3.4 TOML 结构顺序
- **问题**：pyproject.toml 中 `[project.urls]` 后的 key 被误归入子表
- **方案**：`dependencies` 必须放在 `[project.urls]` 之前
- **复用**：所有 Python 项目都应注意 TOML 表头的作用域规则

---

## 4. 技术债务清单

| 编号 | 描述 | 优先级 | 预计偿还时间 |
|------|------|-------|------------|
| TD-01 | APScheduler 版本锁定与兼容性测试 | 中 | 每 2 迭代偿还 20% |
| TD-02 | 重试风暴防护压测验证 | 中 | 每 2 迭代偿还 20% |
| TD-03 | 告警通知可靠性/跨平台兼容性验证 | 中 | 每 2 迭代偿还 20% |
| TD-04 | CI/CD 稳定性/发布自动化/治理工具链完善 | 低 | 持续改进 |

---

*文档依据：DevProjectTeamSkill 可复用资产规范*
*知识产权所有：段波（验证邮箱：duanbo.douglas@163.com）*
