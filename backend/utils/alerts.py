"""
utils/alerts.py — Alert system for critical fault notifications.

Supports console logging (default) and email (optional via SMTP).
Extend this to add PagerDuty, Slack, or MQTT-based alerts.
"""
import logging
import os
import time
from typing import Dict, Any

logger = logging.getLogger(__name__)


async def send_alert(
    severity:    str,
    sensor_id:   str,
    incident_id: str,
    message:     str,
    data:        Dict[str, Any] = {}
) -> bool:
    """
    Send an alert for a critical fault event.

    For CRITICAL/HIGH: logs prominently + could send email.
    For MEDIUM/LOW: logs at warning level.
    
    Returns True if alert was sent successfully.
    """
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    
    alert_text = (
        f"\n{'='*60}\n"
        f"🚨 EMBEDDED SYSTEM FAULT ALERT [{severity.upper()}]\n"
        f"Time:       {ts}\n"
        f"Sensor:     {sensor_id}\n"
        f"Incident:   {incident_id}\n"
        f"Message:    {message}\n"
        f"{'='*60}\n"
    )

    if severity in ("critical", "high"):
        logger.critical(alert_text)
    else:
        logger.warning(alert_text)

    # Email alert (optional — requires SMTP config in .env)
    email_recipient = os.getenv("ALERT_EMAIL")
    if email_recipient and severity in ("critical", "high"):
        try:
            await _send_email(
                to      = email_recipient,
                subject = f"[{severity.upper()}] Embedded System Alert: {sensor_id}",
                body    = alert_text
            )
        except Exception as e:
            logger.error("Email alert failed: %s", e)

    return True


async def _send_email(to: str, subject: str, body: str):
    """Send email via SMTP (configure via SMTP_* env vars)."""
    import smtplib
    from email.mime.text import MIMEText

    host     = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port     = int(os.getenv("SMTP_PORT", "587"))
    user     = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASS", "")

    if not user or not password:
        logger.debug("SMTP not configured, skipping email alert")
        return

    msg            = MIMEText(body)
    msg["Subject"] = subject
    msg["From"]    = user
    msg["To"]      = to

    with smtplib.SMTP(host, port) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)

    logger.info("Email alert sent to %s", to)