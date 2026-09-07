"""  
告警通知模块

负责任务失败、堆积、健康检查异常等场景的告警通知。
支持系统通知（macOS/Linux/Windows）和 Webhook 两种渠道。
支持告警模板、Webhook 重试、告警统计。
"""  

from __future__ import annotations

import json
import subprocess
import urllib.request
import urllib.error
from datetime import datetime
from typing import Optional

from .config import get_config
from .utils import get_logger, is_macos, is_linux, is_windows

logger = get_logger("scheduler.notifier")


# ─── 告警严重级别 ────────────────────────────────────────────

class AlertSeverity:
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


# ─── 告警模板 ──────────────────────────────────────────────

ALERT_TEMPLATES = {
    "task_failed": {
        "title": "任务失败告警: {task_name}",
        "message": "任务 {task_name} 执行失败（第 {retry_count_plus1} 次）\n错误: {error_short}\nRunID: {run_id}",
    },
    "task_dlq": {
        "title": "严重: 任务进入死信队列 - {task_name}",
        "message": "任务 {task_name} 已达到最大重试次数，进入死信队列\n错误: {error_short}\nRunID: {run_id}\n请及时排查处理！",
    },
    "queue_buildup": {
        "title": "告警: 任务队列堆积",
        "message": "待执行任务数: {queue_size}，超过阈值 {threshold}\n请检查调度器状态和任务执行效率。",
    },
    "heartbeat_lost": {
        "title": "严重: 调度器心跳丢失",
        "message": "调度器心跳已超时\n最后心跳: {last_heartbeat}\n请检查调度器进程是否正常运行。",
    },
}


def _render_template(template_name: str, **kwargs) -> tuple[str, str]:
    """渲染告警模板

    Args:
        template_name: 模板名称
        **kwargs: 模板变量

    Returns:
        (title, message) 元组
    """
    tpl = ALERT_TEMPLATES.get(template_name)
    if not tpl:
        return (f"告警: {template_name}", str(kwargs))

    # 提供默认变量
    kwargs.setdefault("error_short", kwargs.get("error_message", "")[:200])
    kwargs.setdefault("retry_count_plus1", kwargs.get("retry_count", 0) + 1)

    title = tpl["title"].format(**kwargs)
    message = tpl["message"].format(**kwargs)
    return title, message


class Notifier:
    """告警通知器

    支持多种通知渠道，可配置启用/禁用。
    """

    def __init__(self):
        config = get_config()
        self.enabled = config.alerting.enabled
        self.system_notification = config.alerting.system_notification
        self.webhook_url = config.alerting.webhook_url
        self.failed_alert_threshold = config.alerting.failed_alert_threshold
        self.queue_alert_threshold = config.alerting.queue_alert_threshold

        # 告警频率控制（避免告警风暴）
        self._last_alert_time: dict[str, datetime] = {}
        self._min_alert_interval = 300  # 同类告警最小间隔 5 分钟

        # Webhook 重试配置
        self._webhook_max_retries = 2
        self._webhook_retry_delay = 5  # 秒

        # 告警统计
        self._alert_stats: dict[str, int] = {
            "total_sent": 0,
            "system_sent": 0,
            "webhook_sent": 0,
            "webhook_failed": 0,
            "suppressed": 0,
        }

    def _should_alert(self, alert_key: str) -> bool:
        """检查是否应该发送告警（频率控制）"""
        now = datetime.now()
        last_time = self._last_alert_time.get(alert_key)
        if last_time and (now - last_time).total_seconds() < self._min_alert_interval:
            return False
        self._last_alert_time[alert_key] = now
        return True

    def alert_task_failed(self, task_name: str, error_message: str,
                          run_id: str = "", retry_count: int = 0):
        """任务失败告警

        Args:
            task_name: 任务名称
            error_message: 错误信息
            run_id: 运行 ID
            retry_count: 重试次数
        """
        if not self.enabled:
            return

        # 达到阈值才告警
        if retry_count + 1 < self.failed_alert_threshold:
            return

        alert_key = f"failed:{task_name}"
        if not self._should_alert(alert_key):
            self._alert_stats["suppressed"] += 1
            return

        title, message = _render_template(
            "task_failed",
            task_name=task_name,
            error_message=error_message,
            run_id=run_id,
            retry_count=retry_count,
        )

        logger.warning(f"ALERT: {title}")
        self._dispatch(title, message, AlertSeverity.WARNING, "task_failed", {
            "task_name": task_name,
            "error_message": error_message,
            "run_id": run_id,
            "retry_count": retry_count,
        })

    def alert_task_dlq(self, task_name: str, error_message: str, run_id: str = ""):
        """任务进入死信队列告警（严重级别）

        Args:
            task_name: 任务名称
            error_message: 错误信息
            run_id: 运行 ID
        """
        if not self.enabled:
            return

        alert_key = f"dlq:{task_name}"
        if not self._should_alert(alert_key):
            self._alert_stats["suppressed"] += 1
            return

        title, message = _render_template(
            "task_dlq",
            task_name=task_name,
            error_message=error_message,
            run_id=run_id,
        )

        logger.critical(f"ALERT DLQ: {task_name}")
        self._dispatch(title, message, AlertSeverity.CRITICAL, "task_dlq", {
            "task_name": task_name,
            "error_message": error_message,
            "run_id": run_id,
        })

    def alert_queue_buildup(self, queue_size: int, threshold: int):
        """队列堆积告警

        Args:
            queue_size: 当前队列大小
            threshold: 阈值
        """
        if not self.enabled:
            return

        alert_key = "queue_buildup"
        if not self._should_alert(alert_key):
            self._alert_stats["suppressed"] += 1
            return

        title, message = _render_template(
            "queue_buildup",
            queue_size=queue_size,
            threshold=threshold,
        )

        logger.warning(f"ALERT: Queue buildup ({queue_size} > {threshold})")
        self._dispatch(title, message, AlertSeverity.WARNING, "queue_buildup", {
            "queue_size": queue_size,
            "threshold": threshold,
        })

    def alert_heartbeat_lost(self, last_heartbeat: str):
        """心跳丢失告警（调度器可能挂了）

        Args:
            last_heartbeat: 最后心跳时间
        """
        if not self.enabled:
            return

        alert_key = "heartbeat_lost"
        if not self._should_alert(alert_key):
            self._alert_stats["suppressed"] += 1
            return

        title, message = _render_template(
            "heartbeat_lost",
            last_heartbeat=last_heartbeat,
        )

        logger.critical("ALERT: Heartbeat lost!")
        self._dispatch(title, message, AlertSeverity.CRITICAL, "heartbeat_lost", {
            "last_heartbeat": last_heartbeat,
        })

    # ─── 统一分发 ───────────────────────────────────────────

    def _dispatch(self, title: str, message: str, severity: str,
                  alert_type: str, data: dict):
        """统一告警分发

        Args:
            title: 告警标题
            message: 告警消息
            severity: 严重级别
            alert_type: 告警类型
            data: 告警数据
        """
        self._alert_stats["total_sent"] += 1
        self._send_system_notification(title, message)
        self._send_webhook_with_retry(alert_type, severity, data)

    def _send_system_notification(self, title: str, message: str):
        """发送系统通知

        跨平台兼容：macOS / Linux / Windows
        """
        if not self.system_notification:
            return

        try:
            if is_macos():
                # macOS: osascript
                script = f'display notification "{message}" with title "{title}"'
                subprocess.run(["osascript", "-e", script],
                             capture_output=True, timeout=5)

            elif is_linux():
                # Linux: notify-send
                subprocess.run(["notify-send", title, message],
                             capture_output=True, timeout=5)

            elif is_windows():
                # Windows: PowerShell Toast Notification
                # 转义特殊字符防止命令注入
                def _ps_escape(s: str) -> str:
                    return s.replace('`', '``').replace('"', '`"').replace('$', '`$')

                safe_title = _ps_escape(title)
                safe_message = _ps_escape(message)
                ps_script = f"""
                [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
                $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
                $toastXml = [xml]$template.GetXml()
                $toastXml.GetElementsByTagName("text").Item(0).AppendChild($toastXml.CreateTextNode("{safe_title}")) > $null
                $toastXml.GetElementsByTagName("text").Item(1).AppendChild($toastXml.CreateTextNode("{safe_message}")) > $null
                $toast = [Windows.UI.Notifications.ToastNotification]::new($toastXml)
                [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Scheduler").Show($toast)
                """
                subprocess.run(
                    ["powershell", "-Command", ps_script],
                    capture_output=True, timeout=10,
                )

        except Exception as e:
            logger.debug(f"System notification failed: {e}")

    def _send_webhook_with_retry(self, alert_type: str, severity: str, data: dict):
        """发送 Webhook 告警（带重试）

        Args:
            alert_type: 告警类型
            severity: 严重级别
            data: 告警数据
        """
        if not self.webhook_url:
            return

        payload = json.dumps({
            "alert_type": alert_type,
            "severity": severity,
            "timestamp": datetime.now().isoformat(),
            "source": "dev-task-scheduler",
            **data,
        }).encode("utf-8")

        for attempt in range(self._webhook_max_retries + 1):
            try:
                req = urllib.request.Request(
                    self.webhook_url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )

                with urllib.request.urlopen(req, timeout=10) as resp:
                    resp.read()

                self._alert_stats["webhook_sent"] += 1
                if attempt > 0:
                    logger.info(f"Webhook succeeded on retry {attempt}")
                return

            except Exception as e:
                if attempt < self._webhook_max_retries:
                    logger.debug(f"Webhook attempt {attempt + 1} failed: {e}, retrying...")
                    import time
                    time.sleep(self._webhook_retry_delay)
                else:
                    self._alert_stats["webhook_failed"] += 1
                    logger.debug(f"Webhook failed after {self._webhook_max_retries + 1} attempts: {e}")

    def get_alert_stats(self) -> dict:
        """获取告警统计

        Returns:
            统计字典
        """
        return dict(self._alert_stats)


# 全局单例
_notifier: Optional[Notifier] = None


def get_notifier() -> Notifier:
    """获取全局通知器单例"""
    global _notifier
    if _notifier is None:
        _notifier = Notifier()
    return _notifier
