"""Alert notification system (Slack, console, webhook).

Adopted from PODO: state-transition-based alerts (no spam).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# State tracking per session (PODO pattern: only alert on transitions)
_last_status: dict[str, str] = {}


class AlertThresholds:
    """Configurable alert thresholds."""

    def __init__(
        self,
        defect_rate_warning: float = 10.0,
        defect_rate_critical: float = 20.0,
        avg_confidence_warning: float = 0.85,
        avg_confidence_critical: float = 0.75,
        false_reject_rate_warning: float = 5.0,
        false_reject_rate_critical: float = 10.0,
    ):
        self.defect_rate_warning = defect_rate_warning
        self.defect_rate_critical = defect_rate_critical
        self.avg_confidence_warning = avg_confidence_warning
        self.avg_confidence_critical = avg_confidence_critical
        self.false_reject_rate_warning = false_reject_rate_warning
        self.false_reject_rate_critical = false_reject_rate_critical


def check_health(
    stats: dict[str, Any],
    thresholds: AlertThresholds | None = None,
) -> dict[str, Any]:
    """Evaluate inspection health and generate alerts.

    Args:
        stats: Current inspection statistics.
        thresholds: Alert thresholds.

    Returns:
        Health status dict with status, alerts, and transition info.
    """
    t = thresholds or AlertThresholds()
    alerts = []

    defect_rate = stats.get("defect_rate", 0)
    if defect_rate >= t.defect_rate_critical:
        alerts.append({"level": "critical", "metric": "defect_rate", "value": defect_rate, "threshold": t.defect_rate_critical})
    elif defect_rate >= t.defect_rate_warning:
        alerts.append({"level": "warning", "metric": "defect_rate", "value": defect_rate, "threshold": t.defect_rate_warning})

    false_reject_rate = stats.get("false_reject_rate", 0)
    if false_reject_rate >= t.false_reject_rate_critical:
        alerts.append({"level": "critical", "metric": "false_reject_rate", "value": false_reject_rate, "threshold": t.false_reject_rate_critical})
    elif false_reject_rate >= t.false_reject_rate_warning:
        alerts.append({"level": "warning", "metric": "false_reject_rate", "value": false_reject_rate, "threshold": t.false_reject_rate_warning})

    # Determine overall status
    if any(a["level"] == "critical" for a in alerts):
        status = "critical"
    elif any(a["level"] == "warning" for a in alerts):
        status = "warning"
    else:
        status = "healthy"

    return {"status": status, "alerts": alerts, "timestamp": datetime.now(timezone.utc).isoformat()}


def check_and_notify(
    session_id: str,
    stats: dict[str, Any],
    thresholds: AlertThresholds | None = None,
    slack_webhook: str | None = None,
) -> dict[str, Any]:
    """Check health and send notification only on state transitions (PODO pattern).

    Args:
        session_id: Current session identifier.
        stats: Inspection statistics.
        thresholds: Alert thresholds.
        slack_webhook: Slack webhook URL (or SLACK_WEBHOOK_URL env var).

    Returns:
        Health status dict.
    """
    health = check_health(stats, thresholds)
    current_status = health["status"]

    # State transition detection (PODO pattern: prevent alert spam)
    tracking_key = session_id or "__global__"
    prev_status = _last_status.get(tracking_key, "healthy")

    if current_status != prev_status:
        transition = f"{prev_status} → {current_status}"
        health["status_change"] = transition
        logger.warning("Health transition [%s]: %s", tracking_key, transition)

        # Send notification
        webhook = slack_webhook or os.environ.get("SLACK_WEBHOOK_URL")
        if webhook:
            _send_slack_alert(webhook, health, session_id, transition)
        else:
            _log_alert(health, session_id, transition)

        _last_status[tracking_key] = current_status

    return health


def _send_slack_alert(
    webhook_url: str,
    health: dict[str, Any],
    session_id: str,
    transition: str,
) -> None:
    """Send Slack alert via webhook (PODO pattern)."""
    try:
        import urllib.request

        status = health["status"]
        emoji = {"healthy": "✅", "warning": "⚠️", "critical": "🚨"}.get(status, "❓")

        alert_lines = []
        for a in health.get("alerts", []):
            level_icon = "🔴" if a["level"] == "critical" else "🟡"
            alert_lines.append(f"{level_icon} {a['metric']}: {a['value']:.1f}% (threshold: {a['threshold']:.1f}%)")

        text = (
            f"{emoji} *PCB Inspection Alert*\n"
            f"*Status:* {status.upper()} ({transition})\n"
            f"*Session:* {session_id}\n"
        )
        if alert_lines:
            text += "*Alerts:*\n" + "\n".join(alert_lines)

        payload = json.dumps({"text": text}).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5)
        logger.info("Slack alert sent: %s", transition)

    except Exception:
        logger.exception("Failed to send Slack alert")


def _log_alert(
    health: dict[str, Any],
    session_id: str,
    transition: str,
) -> None:
    """Log alert to console when Slack is not configured."""
    status = health["status"]
    alerts = health.get("alerts", [])
    logger.warning(
        "ALERT [%s] %s → %s | alerts: %s",
        session_id, transition.split(" → ")[0], status,
        ", ".join(f"{a['metric']}={a['value']:.1f}%" for a in alerts),
    )
