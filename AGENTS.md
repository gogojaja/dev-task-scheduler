# dev-task-scheduler — AI Agent 指令

> 项目群归属：PG-LOCAL-001（Douglas 项目群），PMO = DevProjectTeamSkill (role-program-mgmt)
> 审核日期：2026-09-05（项目群边界审核 B-04）

## 职责边界（铁律）

### 定位

本地定时/计划任务可观测调度框架。

### 职责

- 承载任务调度实现：注册/触发/执行/重试/幂等/状态持久化
- 承载调度可观测性（执行记录/状态查询/审计日志）

### 禁做

- 禁止承载非调度领域的功能
- 禁止直接管理项目级台账或角色包

### 对外接口

- 对外提供 scheduler/cli.py 命令行接口（供 DevProjectTeamSkill scheduler_proxy 转发）
- DEV_TASK_SCHEDULER_ROOT / .scheduler_root 用于被代理方定位

### 依赖

- 标准库 + SQLite（无外部依赖）

### 项目群归属

- PGO（项目群整体视图与管控）：DevProjectTeamSkill (role-program-mgmt)
- 本项目类型：工具库
- 本项目由 DevProjectTeamSkill 角色包按需调用，不独立承载项目群管理功能
