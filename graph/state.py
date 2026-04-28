from __future__ import annotations

import operator
from typing import Annotated, List, Optional

from langchain_core.messages import BaseMessage
from typing_extensions import TypedDict


class AgentState(TypedDict):
    """Shared state flowing through every node in the LangGraph multi-agent workflow.

    The `messages` field uses an append reducer: each node's returned list is
    concatenated onto the existing history rather than replacing it.

    Fields:
        messages:      Full conversation history including HumanMessage,
                       AIMessage, and ToolMessage objects.
        plan:          Numbered execution plan produced by the Planner agent.
                       None until planner_node has run.
        iteration:     Number of executor→tools→executor cycles completed.
                       Enforces a safety ceiling via MAX_ITERATIONS in executor.
        error:         Last error string encountered by any node; None if clean.
        task_complete: Set to True by the Reviewer when the task passes review.
        scenario:      Optional tag identifying the active example scenario
                       (e.g. 'customer_support', 'research', 'monitoring').
    """

    messages: Annotated[List[BaseMessage], operator.add]
    plan: Optional[str]
    iteration: int
    error: Optional[str]
    task_complete: bool
    scenario: Optional[str]
