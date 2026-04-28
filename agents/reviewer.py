"""
Reviewer agent node — evaluates whether the executor fully completed the task.

Reads the entire conversation history and emits a verdict with a mandatory prefix:
  "APPROVED:" — task is done, workflow ends.
  "REVISION NEEDED:" — executor must retry; workflow loops back.
  "FAILED:" — critical error; workflow ends with failure.
"""
import logging

from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI

import config
from graph.state import AgentState

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a quality reviewer in a multi-agent AI system.

Assess whether the executor fully and correctly completed the user's original request.

Evaluation criteria:
1. Were all plan steps executed in order?
2. Did any step return an unhandled error?
3. Is the final output coherent, complete, and accurate?
4. Does the output directly satisfy the user's original message?

Required output format — begin with EXACTLY one of these prefixes:
  "APPROVED: ..."        — task is complete and correct; follow with a brief summary.
  "REVISION NEEDED: ..."  — task is incomplete; list specific missing or incorrect steps.
  "FAILED: ..."           — a critical error occurred; describe what went wrong.

Do not approve a task if any tool call returned an unhandled ERROR.
"""


def reviewer_node(state: AgentState) -> dict:
    """Review the full conversation history and emit an approval verdict.

    The ``task_complete`` flag is set by inspecting the response prefix —
    "APPROVED:" means the reviewer accepted the work, any other prefix means
    the workflow should loop or terminate.

    Args:
        state: Current AgentState with the complete message history.

    Returns:
        Partial AgentState update with the reviewer's AIMessage appended and
        ``task_complete`` set accordingly.
    """
    llm = ChatOpenAI(model=config.LLM_MODEL, temperature=config.LLM_TEMPERATURE)
    messages_with_system = [SystemMessage(content=_SYSTEM_PROMPT)] + list(state["messages"])

    try:
        response = llm.invoke(messages_with_system)
    except Exception as exc:
        logger.error("reviewer_node failed: %s", exc)
        from langchain_core.messages import AIMessage
        response = AIMessage(content=f"FAILED: reviewer error — {exc}")

    content = response.content.strip()
    task_complete = content.startswith("APPROVED:")
    logger.info("Review result: %s", content[:120])

    return {
        "messages": [response],
        "task_complete": task_complete,
    }
