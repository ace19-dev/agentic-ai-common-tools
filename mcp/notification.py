"""
Notification MCP — email (SMTP), Slack webhook, and console output.

Channel selection:
  - Email: port 465 → SMTP_SSL (implicit TLS); port 587 → SMTP + STARTTLS.
  - Slack: posts a JSON payload to the incoming webhook URL.
  - Console: always available; used as a dry-run fallback for the other channels.

When dry_run=True or credentials are absent the MCP silently falls back to
console output, making the system safe to run without external accounts.
"""
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import requests

import config
from core.base_mcp import BaseMCP, MCPResult

logger = logging.getLogger(__name__)


class NotificationMCP(BaseMCP):
    """Multi-channel notification service: SMTP email, Slack webhook, and console.

    When dry_run=True (or when credentials are absent), all sends fall back to
    console output so the system remains fully testable without external accounts.
    """

    def __init__(self,
                 smtp_host: str = "",
                 smtp_port: int = 587,
                 smtp_user: str = "",
                 smtp_password: str = "",
                 smtp_from: str = "",
                 slack_webhook_url: str = "",
                 dry_run: bool = False):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_password = smtp_password
        self.smtp_from = smtp_from or smtp_user
        self.slack_webhook_url = slack_webhook_url
        self.dry_run = dry_run

    # ── Email ─────────────────────────────────────────────────────────────────

    def email(self, to: str, subject: str, body: str, html: bool = False) -> MCPResult:
        if self.dry_run or not self.smtp_host:
            preview = f"[DRY-RUN EMAIL] To={to!r} | Subject={subject!r}\n{body[:300]}"
            logger.info(preview)
            print(preview)
            return MCPResult.ok(data="dry_run")
        try:
            mime = MIMEMultipart("alternative")
            mime["Subject"] = subject
            mime["From"] = self.smtp_from
            mime["To"] = to
            mime.attach(MIMEText(body, "html" if html else "plain", "utf-8"))

            # Port 465 uses implicit TLS from the start (SMTP_SSL).
            # Port 587 uses plaintext upgraded via STARTTLS after EHLO.
            if self.smtp_port == 465:
                with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port) as srv:
                    srv.login(self.smtp_user, self.smtp_password)
                    srv.sendmail(self.smtp_from, [to], mime.as_string())
            else:
                with smtplib.SMTP(self.smtp_host, self.smtp_port) as srv:
                    srv.ehlo()
                    srv.starttls()
                    srv.login(self.smtp_user, self.smtp_password)
                    srv.sendmail(self.smtp_from, [to], mime.as_string())
            logger.info("Email sent → %s: %s", to, subject)
            return MCPResult.ok(data="sent")
        except smtplib.SMTPAuthenticationError:
            return MCPResult.fail("SMTP authentication failed — check credentials in .env")
        except smtplib.SMTPException as exc:
            return MCPResult.fail(f"SMTP error: {exc}")
        except Exception as exc:
            logger.error("notification.email failed: %s", exc)
            return MCPResult.fail(str(exc))

    # ── Slack ─────────────────────────────────────────────────────────────────

    def slack(self, channel: str, message: str,
              webhook_url: Optional[str] = None) -> MCPResult:
        url = webhook_url or self.slack_webhook_url
        if self.dry_run or not url:
            preview = f"[DRY-RUN SLACK] #{channel}: {message}"
            logger.info(preview)
            print(preview)
            return MCPResult.ok(data="dry_run")
        try:
            resp = requests.post(url, json={"text": message, "channel": channel}, timeout=10)
            if resp.status_code == 200 and resp.text == "ok":
                return MCPResult.ok(data="sent")
            return MCPResult.fail(f"Slack webhook returned {resp.status_code}: {resp.text[:200]}")
        except Exception as exc:
            logger.error("notification.slack failed: %s", exc)
            return MCPResult.fail(str(exc))

    # ── Console ───────────────────────────────────────────────────────────────

    def console(self, level: str, message: str) -> MCPResult:
        level_upper = level.upper()
        log_fn = getattr(logger, level_upper.lower(), logger.info)
        formatted = f"[{level_upper}] {message}"
        log_fn(formatted)
        print(formatted)
        return MCPResult.ok(data="printed")

    # ── Health ────────────────────────────────────────────────────────────────

    def health_check(self) -> MCPResult:
        return MCPResult.ok(data={
            "mcp": "notification",
            "dry_run": self.dry_run,
            "smtp_configured": bool(self.smtp_host and self.smtp_user),
            "slack_configured": bool(self.slack_webhook_url),
        })


# ── Module-level singleton ────────────────────────────────────────────────────

_instance: Optional[NotificationMCP] = None


def get_notification_mcp() -> NotificationMCP:
    """Return the process-wide NotificationMCP singleton, creating it on first call."""
    global _instance
    if _instance is None:
        _instance = NotificationMCP(
            smtp_host=config.SMTP_HOST,
            smtp_port=config.SMTP_PORT,
            smtp_user=config.SMTP_USER,
            smtp_password=config.SMTP_PASSWORD,
            smtp_from=config.SMTP_FROM,
            slack_webhook_url=config.SLACK_WEBHOOK_URL,
            dry_run=config.NOTIFICATION_DRY_RUN,
        )
    return _instance
