"""
告警通知模块

负责任务失败、堆积、健康检查异常等场景的告警通知。
支持系统通知（macOS/Linux/Windows）和 Webhook 两种渠道。
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from typing import Optional

from .config import get_config
from .utils import get_logger, is_macos, is_linux, is_windows

logger = get_logger("scheduler.notifier")


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
            return

        title = f"任务失败告警: {task_name}"
        message = f"任务 {task_name} 执行失败（第 {retry_count + 1} 次）\n错误: {error_message[:200]}\nRunID: {run_id}"

        logger.warning(f"ALERT: {title}")
        self._send_system_notification(title, message)
        self._send_webhook("task_failed", {
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
            return

        title = f"严重: 任务进入死信队列 - {task_name}"
        message = f"任务 {task_name} 已达到最大重试次数，进入死信队列\n错误: {error_message[:200]}\nRunID: {run_id}\n请及时排查处理！"

        logger.critical(f"ALERT DLQ: {task_name}")
        self._send_system_notification(title, message)
        self._send_webhook("task_dlq", {
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
            return

        title = "告警: 任务队列堆积"
        message = f"待执行任务数: {queue_size}，超过阈值 {threshold}\n请检查调度器状态和任务执行效率。"

        logger.warning(f"ALERT: Queue buildup ({queue_size} > {threshold})")
        self._send_system_notification(title, message)
        self._send_webhook("queue_buildup", {
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
            return

        title = "严重: 调度器心跳丢失"
        message = f"调度器心跳已超时\n最后心跳: {last_heartbeat}\n请检查调度器进程是否正常运行。"

        logger.critical("ALERT: Heartbeat lost!")
        self._send_system_notification(title, message)
        self._send_webhook("heartbeat_lost", {
            "last_heartbeat": last_heartbeat,
        })

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
                ps_script = f"""
                [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
                $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
                $toastXml = [xml]$template.GetXml()
                $toastXml.GetElementsByTagName("text").Item(0).AppendChild($toastXml.CreateTextNode("{title}")) > $null
                $toastXml.GetElementsByTagName("text").Item(1).AppendChild($toastXml.CreateTextNode("{message}")) > $null
                $toast = [Windows.UI.Notifications.ToastNotification]::new($toastXml)
                [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Scheduler").Show($toast)
                """
                subprocess.run(
                    ["powershell", "-Command", ps_script],
                    capture_output=True, timeout=10,
                )

        except Exception as e:
            logger.debug(f"System notification failed: {e}")

    def _send_webhook(self, alert_type: str, data: dict):
        """发送 Webhook 告警（可选）

        支持通用 webhook，POST JSON 数据。
        """
        if not self.webhook_url:
            return

        try:
            import urllib.request

            payload = json.dumps({
                "alert_type": alert_type,
                "timestamp": datetime.now().isoformat(),
                **data,
            }).encode("utf-8")

            req = urllib.request.Request(
                self.webhook_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()

        except Exception as e:
            logger.debug(f"Webhook notification failed: {e}")


# 全局单例
_notifier: Optional[Notifier] = None


def get_notifier() -> Notifier:
    """获取全局通知器单例"""
    global _notifier
    if _notifier is None:
        _notifier = Notifier()
    return _notifier
