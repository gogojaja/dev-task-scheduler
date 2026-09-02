"""
任务执行框架

负责任务的实际执行，包括：
- 任务加载与调用
- 状态机管理
- 超时控制
- 错误处理与重试决策
- 幂等校验集成
- 执行记录写入
"""

from __future__ import annotations

import signal
import traceback
import threading
from datetime import datetime
from typing import Callable, Optional

from .models import (
    TaskResult, TaskContext, ExecutionRecord,
    TaskStatus, ErrorCode, TaskDefinition,
)
from .state_store import get_state_store, StateStore
from .record_writer import get_record_writer, RecordWriter
from .idempotency import get_idempotency_manager, IdempotencyManager
from .retry import get_retry_policy, RetryPolicy
from .utils import get_logger, load_function, now, get_node_id, gen_uuid
from .timeout import run_with_thread_timeout, TimeoutError as TaskTimeoutError

logger = get_logger("scheduler.executor")


class TaskExecutor:
    """任务执行器

    负责任务的实际执行、状态管理、错误处理。
    """

    def __init__(
        self,
        store: StateStore = None,
        record_writer: RecordWriter = None,
        idempotency_mgr: IdempotencyManager = None,
        retry_policy: RetryPolicy = None,
    ):
        self.store = store or get_state_store()
        self.record_writer = record_writer or get_record_writer()
        self.idempotency = idempotency_mgr or get_idempotency_manager()
        self.retry_policy = retry_policy or get_retry_policy()

    def execute_task(
        self,
        task_def: TaskDefinition,
        trigger_type: str = "schedule",
        scheduled_time: datetime = None,
        params: dict = None,
    ) -> TaskResult:
        """执行任务

        完整的执行流程：
        1. 创建执行上下文
        2. 生成幂等键
        3. 幂等检查（已成功则跳过）
        4. 创建执行记录
        5. 执行业务逻辑（含超时控制）
        6. 处理结果（成功/失败）
        7. 更新执行记录
        8. 写入 CSV 台账

        Args:
            task_def: 任务定义
            trigger_type: 触发方式 schedule/manual/retry
            scheduled_time: 计划执行时间
            params: 任务参数

        Returns:
            执行结果
        """
        context = TaskContext.create(
            task_name=task_def.name,
            scheduled_time=scheduled_time or now(),
            params={**task_def.params, **(params or {})},
        )

        logger.info(f"Executing task: {task_def.name} (run_id={context.run_id}, trigger={trigger_type})")

        # 1. 生成幂等键
        idempotency_key = self.idempotency.generate_key(
            task_name=task_def.name,
            idempotency_expr=task_def.idempotency_key_expr,
            context=context,
        )
        context.idempotency_key = idempotency_key

        # 2. 幂等检查
        cached_result = self.idempotency.check(idempotency_key)
        if cached_result is not None:
            logger.info(f"Task skipped due to idempotency: {task_def.name} ({idempotency_key})")
            self._record_skipped(task_def, context, idempotency_key, cached_result)
            return TaskResult.ok(
                message="Skipped: idempotent duplicate",
                data=cached_result,
            )

        # 3. 创建执行记录（running 状态）
        record = ExecutionRecord(
            run_id=context.run_id,
            task_name=task_def.name,
            trigger_type=trigger_type,
            scheduled_time=context.scheduled_time,
            start_time=context.start_time,
            status=TaskStatus.RUNNING.value,
            retry_count=context.retry_count,
            idempotency_key=idempotency_key,
            node_id=get_node_id(),
        )
        exec_id = self.store.create_execution(record)

        # 4. 执行业务逻辑
        try:
            result = self._run_with_timeout(
                task_def=task_def,
                context=context,
                timeout=task_def.timeout,
            )
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            logger.error(f"Task execution error: {task_def.name} - {error_msg}")
            logger.debug(traceback.format_exc())
            result = TaskResult.fail(
                message=error_msg,
                error_code=ErrorCode.EXECUTION_ERROR,
                skip_retry=False,
            )

        # 5. 处理结果
        end_time = now()
        duration = (end_time - context.start_time).total_seconds()

        if result.success:
            self._handle_success(
                task_def=task_def,
                context=context,
                result=result,
                exec_id=exec_id,
                idempotency_key=idempotency_key,
                end_time=end_time,
                duration=duration,
            )
        else:
            self._handle_failure(
                task_def=task_def,
                context=context,
                result=result,
                exec_id=exec_id,
                idempotency_key=idempotency_key,
                end_time=end_time,
                duration=duration,
            )

        return result

    def execute_with_retry(
        self,
        task_def: TaskDefinition,
        trigger_type: str = "manual",
        scheduled_time: datetime = None,
        params: dict = None,
    ) -> TaskResult:
        """带重试的执行任务

        在当前线程内执行重试循环，每次重试之间等待退避延迟。

        Args:
            task_def: 任务定义
            trigger_type: 触发方式
            scheduled_time: 计划执行时间
            params: 任务参数

        Returns:
            最终执行结果
        """
        max_retries = task_def.max_retries
        context = TaskContext.create(
            task_name=task_def.name,
            scheduled_time=scheduled_time or now(),
            params={**task_def.params, **(params or {})},
        )

        for attempt in range(max_retries + 1):
            context.retry_count = attempt

            result = self.execute_task(
                task_def=task_def,
                trigger_type="retry" if attempt > 0 else trigger_type,
                scheduled_time=scheduled_time,
                params=params,
            )

            if result.success:
                return result

            # 判断是否应该继续重试
            if self.retry_policy.is_dead_letter(attempt, skip_retry=result.skip_retry):
                logger.critical(
                    f"Task entered DLQ after {attempt} retries: {task_def.name}"
                )
                return result

            # 计算重试延迟
            delay = self.retry_policy.calculate_delay(attempt)
            logger.info(
                f"Task retry {attempt + 1}/{max_retries} in {delay:.1f}s: {task_def.name}"
            )

            # 等待重试（线程休眠）
            import time
            time.sleep(delay)

        return result

    def _run_with_timeout(self, task_def: TaskDefinition,
                          context: TaskContext, timeout: int) -> TaskResult:
        """带超时控制地执行任务

        使用线程超时实现（跨平台兼容）。
        可通过 timeout.py 选择信号/进程级超时。
        """
        func = load_function(task_def.func_ref)

        # 确定函数签名
        import inspect
        sig = inspect.signature(func)
        use_context = len(sig.parameters) > 0

        def target():
            if use_context:
                return func(context)
            else:
                return func()

        try:
            raw_result = run_with_thread_timeout(
                target, timeout=timeout,
            )
        except TaskTimeoutError:
            logger.warning(f"Task timeout: {task_def.name} (timeout={timeout}s)")
            return TaskResult.fail(
                message=f"Task timed out after {timeout} seconds",
                error_code=ErrorCode.TASK_TIMEOUT,
                skip_retry=False,
            )

        # 处理返回值
        if isinstance(raw_result, TaskResult):
            return raw_result
        elif raw_result is None or raw_result is True:
            return TaskResult.ok()
        elif isinstance(raw_result, dict):
            return TaskResult.ok(data=raw_result)
        elif isinstance(raw_result, str):
            return TaskResult.ok(message=raw_result)
        else:
            return TaskResult.ok(data={"value": raw_result})

    def _handle_success(self, task_def: TaskDefinition, context: TaskContext,
                        result: TaskResult, exec_id: int, idempotency_key: str,
                        end_time: datetime, duration: float):
        """处理执行成功"""
        logger.info(f"Task succeeded: {task_def.name} ({duration:.2f}s)")

        # 更新数据库状态
        self.store.update_execution(
            run_id=context.run_id,
            status=TaskStatus.SUCCESS.value,
            end_time=end_time,
            duration=duration,
            result_data=result.data,
        )

        # 记录幂等键
        self.idempotency.record(
            job_id=0,  # 会通过 task_name 查找
            idempotency_key=idempotency_key,
            result_data=result.data,
            execution_id=exec_id,
        )

        # 写入 CSV 台账
        record = ExecutionRecord(
            id=exec_id,
            run_id=context.run_id,
            task_name=task_def.name,
            trigger_type=context.params.get("_trigger_type", "schedule"),
            scheduled_time=context.scheduled_time,
            start_time=context.start_time,
            end_time=end_time,
            duration=duration,
            status=TaskStatus.SUCCESS.value,
            retry_count=context.retry_count,
            idempotency_key=idempotency_key,
            error_code=result.error_code,
            error_message=result.message,
            result_data=result.data,
            node_id=get_node_id(),
        )
        self.record_writer.append_record(record)

    def _handle_failure(self, task_def: TaskDefinition, context: TaskContext,
                        result: TaskResult, exec_id: int, idempotency_key: str,
                        end_time: datetime, duration: float):
        """处理执行失败"""
        logger.error(f"Task failed: {task_def.name} - {result.message} ({duration:.2f}s)")

        # 判断是否进入死信
        is_dlq = self.retry_policy.is_dead_letter(
            retry_count=context.retry_count,
            skip_retry=result.skip_retry,
        )

        status = TaskStatus.DLQ.value if is_dlq else TaskStatus.FAILED.value

        # 更新数据库状态
        self.store.update_execution(
            run_id=context.run_id,
            status=status,
            end_time=end_time,
            duration=duration,
            error_code=result.error_code,
            error_message=result.message,
        )

        # 写入 CSV 台账
        record = ExecutionRecord(
            id=exec_id,
            run_id=context.run_id,
            task_name=task_def.name,
            trigger_type=context.params.get("_trigger_type", "schedule"),
            scheduled_time=context.scheduled_time,
            start_time=context.start_time,
            end_time=end_time,
            duration=duration,
            status=status,
            retry_count=context.retry_count,
            idempotency_key=idempotency_key,
            error_code=result.error_code,
            error_message=result.message,
            result_data=result.data,
            node_id=get_node_id(),
        )
        self.record_writer.append_record(record)

        if is_dlq:
            logger.critical(f"Task entered DLQ: {task_def.name} (retries={context.retry_count})")

    def _record_skipped(self, task_def: TaskDefinition, context: TaskContext,
                        idempotency_key: str, cached_result: dict):
        """记录幂等跳过"""
        end_time = now()
        duration = (end_time - context.start_time).total_seconds()

        record = ExecutionRecord(
            run_id=context.run_id,
            task_name=task_def.name,
            trigger_type="schedule",
            scheduled_time=context.scheduled_time,
            start_time=context.start_time,
            end_time=end_time,
            duration=duration,
            status=TaskStatus.SKIPPED.value,
            retry_count=context.retry_count,
            idempotency_key=idempotency_key,
            error_message="Idempotent duplicate",
            result_data=cached_result,
            node_id=get_node_id(),
        )
        self.store.create_execution(record)
        self.record_writer.append_record(record)


# 全局单例
_executor: Optional[TaskExecutor] = None


def get_executor() -> TaskExecutor:
    """获取全局执行器单例"""
    global _executor
    if _executor is None:
        _executor = TaskExecutor()
    return _executor
