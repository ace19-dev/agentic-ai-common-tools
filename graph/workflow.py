"""
General-purpose multi-agent LangGraph workflow.

Topology:
    START → planner → executor ──[tool_calls?]──► tools ──► executor
                                                              │
                                          ◄──[revision]───────┤
                                                              ▼
                                                         reviewer ──► END
"""
import logging

from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from agents.executor import executor_node, tools as executor_tools
from agents.planner import planner_node
from agents.reviewer import reviewer_node
from graph.state import AgentState

logger = logging.getLogger(__name__)

_MAX_REVIEW_CYCLES = 3


def _route_after_executor(state: AgentState) -> str:
    """Send to 'tools' if there are pending tool calls, otherwise to 'reviewer'."""
    if state.get("error") == "max_iterations_exceeded":
        return "reviewer"
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return "reviewer"


def _route_after_reviewer(state: AgentState) -> str:
    """Approve → END. Revision needed → executor. Hard cap prevents infinite loops."""
    if state.get("task_complete"):
        return END
    iteration = state.get("iteration", 0)
    # Hard cap: _MAX_REVIEW_CYCLES * 10 covers the planner + up to 10 tool
    # calls per executor pass, multiplied by the number of allowed revisions.
    if iteration >= _MAX_REVIEW_CYCLES * 10:
        logger.warning("Reviewer loop cap reached — forcing END")
        return END
    content = state["messages"][-1].content.strip()
    if content.startswith("REVISION NEEDED:"):
        return "executor"
    return END


def build_graph(scenario: str = ""):
    """Construct and compile the multi-agent LangGraph workflow.

    Graph topology:
        START → planner → executor ──[tool_calls?]──► tools ─┐
                              ▲                                │
                              └──────────────[revision]───────┘
                          executor ──[done]──► reviewer ──[approved/failed]──► END

    Args:
        scenario: Optional tag propagated to AgentState for observability.

    Returns:
        A compiled LangGraph `CompiledGraph` ready for `.invoke()` or `.stream()`.
    """
    tool_node = ToolNode(executor_tools)

    graph = StateGraph(AgentState)
    graph.add_node("planner", planner_node)
    graph.add_node("executor", executor_node)
    graph.add_node("tools", tool_node)
    graph.add_node("reviewer", reviewer_node)

    graph.set_entry_point("planner")
    graph.add_edge("planner", "executor")

    graph.add_conditional_edges(
        "executor",
        _route_after_executor,
        {"tools": "tools", "reviewer": "reviewer"},
    )

    graph.add_edge("tools", "executor")

    graph.add_conditional_edges(
        "reviewer",
        _route_after_reviewer,
        {"executor": "executor", END: END},
    )

    return graph.compile()
