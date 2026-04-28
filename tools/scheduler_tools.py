"""
LangChain @tool wrappers for the Scheduler MCP (APScheduler + SQLite).

trigger_args is a JSON string so the LLM can pass cron/interval/date
configurations without requiring a nested dict in the tool schema.
"""
import json
from langchain_core.tools import tool
from mcp.scheduler import get_scheduler_mcp

_mcp = get_scheduler_mcp()


@tool
def schedule_create(job_id: str, func_name: str,
                    trigger: str, trigger_args: str,
                    kwargs: str = "{}") -> str:
    """Schedule a recurring or one-time background job.

    The function to execute must be pre-registered in the SchedulerMCP registry.
    Jobs are persisted in SQLite and survive process restarts.

    Args:
        job_id: Unique identifier for this job (used for listing and removal).
        func_name: Name of a pre-registered function
                   (e.g. 'health_check_all', 'send_daily_report').
        trigger: Scheduling strategy — one of:
                   'interval' for periodic execution,
                   'cron'     for calendar-based execution,
                   'date'     for a single one-time execution.
        trigger_args: JSON string of trigger arguments.
                   interval → '{"seconds": 30}' or '{"minutes": 5}'
                   cron    → '{"hour": "*/2", "minute": "0"}'
                   date    → '{"run_date": "2026-05-01 09:00:00"}'
        kwargs: JSON string of keyword arguments forwarded to the function.

    Returns:
        JSON with 'job_id' and 'status': 'scheduled', or 'ERROR: ...'.
    """
    try:
        t_args = json.loads(trigger_args)
    except json.JSONDecodeError:
        return "ERROR: trigger_args must be a valid JSON string"
    try:
        kw = json.loads(kwargs) if kwargs else {}
    except json.JSONDecodeError:
        kw = {}
    return _mcp.create(job_id, func_name, trigger, t_args, kw).to_tool_str()


@tool
def schedule_list() -> str:
    """List all currently active scheduled jobs.

    Returns:
        JSON array of job objects, each with:
          - 'id' (str): job identifier
          - 'func_name' (str): registered function name
          - 'trigger' (str): trigger type
          - 'trigger_args' (dict): trigger configuration
          - 'next_run_time' (str|null): ISO datetime of the next execution
        Returns '[]' if no active jobs exist.
    """
    return _mcp.list_jobs().to_tool_str()


@tool
def schedule_remove(job_id: str) -> str:
    """Cancel and permanently remove a scheduled job.

    Args:
        job_id: The identifier of the job to remove.

    Returns:
        'removed' on success, or 'ERROR: job not found'.
    """
    return _mcp.remove(job_id).to_tool_str()
