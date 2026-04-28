"""
LangChain @tool wrappers for the Notification MCP (email, Slack, console).

All three tools are safe to call in dry-run mode (NOTIFICATION_DRY_RUN=true),
which is the default — they print to console instead of contacting external
services, making the system fully testable without real credentials.
"""
from langchain_core.tools import tool
from mcp.notification import get_notification_mcp

_mcp = get_notification_mcp()


@tool
def notify_email(to: str, subject: str, body: str) -> str:
    """Send an email notification via SMTP.

    Use this to deliver summaries, alerts, or reports to a human recipient.
    In dry-run mode (NOTIFICATION_DRY_RUN=true) or when SMTP is not configured,
    the email content is printed to the console instead.

    Args:
        to: Recipient email address (e.g. 'user@example.com').
        subject: Email subject line. Keep under 100 characters.
        body: Plain-text email body. Can be multi-line.

    Returns:
        'sent' on successful delivery,
        'dry_run' when running without SMTP credentials,
        or 'ERROR: ...' on SMTP failure.
    """
    return _mcp.email(to, subject, body).to_tool_str()


@tool
def notify_slack(channel: str, message: str) -> str:
    """Post a message to a Slack channel via incoming webhook.

    In dry-run mode or when SLACK_WEBHOOK_URL is not configured, the message
    is printed to the console instead of being sent.

    Args:
        channel: Slack channel name or ID (e.g. '#alerts' or '#general').
                 Must include the '#' prefix for named channels.
        message: Message text. Supports Slack mrkdwn:
                 *bold*, _italic_, `code`, >blockquote.

    Returns:
        'sent' on success,
        'dry_run' when running without Slack credentials,
        or 'ERROR: ...' on webhook failure.
    """
    return _mcp.slack(channel, message).to_tool_str()


@tool
def notify_console(level: str, message: str) -> str:
    """Print a structured log message to the console.

    Always executes — never suppressed by dry-run mode. Use for progress
    updates, status logging, or debug output during agent execution.

    Args:
        level: Severity level — one of 'INFO', 'WARNING', 'ERROR', 'DEBUG'.
        message: The message text to log.

    Returns:
        'printed' always.
    """
    return _mcp.console(level, message).to_tool_str()
