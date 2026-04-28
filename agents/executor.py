"""
Executor agent node — follows the planner's steps by calling tools.

Binds all 19 domain tools to the LLM so tool calls are handled by LangGraph's
ToolNode.  Guards against runaway loops with MAX_ITERATIONS.
"""
import logging

from langchain_core.messages import AIMessage, SystemMessage
from langchain_openai import ChatOpenAI

import config
from graph.state import AgentState
from tools import ALL_TOOLS

logger = logging.getLogger(__name__)

# Caps executor→tools→executor cycles per workflow run.
# 10 allows complex multi-step tasks while preventing unbounded LLM spend.
MAX_ITERATIONS = 10

_SYSTEM_PROMPT = """You are an executor agent in a multi-agent AI system.
Your job is to follow the plan exactly and call the appropriate tools to complete it.

Rules:
1. Execute plan steps in order — do not skip steps.
2. After every tool call, inspect the result before proceeding to the next step.
3. If a tool returns 'ERROR: ...', report it clearly and stop further steps.
4. Never invent data — use only what the tools return.
5. When all plan steps are complete, end with:
   "EXECUTION COMPLETE: <one-sentence summary of what was accomplished>"
"""

tools = ALL_TOOLS


def executor_node(state: AgentState) -> dict:
    """Execute one step of the plan, calling tools as needed.

    Each invocation increments ``iteration``.  When the LLM's response contains
    tool_calls, LangGraph routes to ToolNode and then back here; when it doesn't,
    the router sends execution to the reviewer.

    Args:
        state: Current AgentState including accumulated messages and iteration count.

    Returns:
        Partial AgentState update with the LLM's response appended to messages
        and iteration incremented.  Sets error="max_iterations_exceeded" when
        the cap is hit so the reviewer can still run and close the cycle cleanly.
    """
    iteration = state.get("iteration", 0)

    if iteration >= MAX_ITERATIONS:
        msg = AIMessage(
            content=f"EXECUTION HALTED: maximum iteration limit ({MAX_ITERATIONS}) reached."
        )
        return {
            "messages": [msg],
            "error": "max_iterations_exceeded",
        }

    llm = ChatOpenAI(model=config.LLM_MODEL, temperature=config.LLM_TEMPERATURE)
    llm_with_tools = llm.bind_tools(tools)

    messages_with_system = [SystemMessage(content=_SYSTEM_PROMPT)] + list(state["messages"])

    try:
        response = llm_with_tools.invoke(messages_with_system)
    except Exception as exc:
        logger.error("executor_node failed: %s", exc)
        response = AIMessage(content=f"EXECUTOR ERROR: {exc}")

    return {
        "messages": [response],
        "iteration": iteration + 1,
    }
