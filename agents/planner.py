"""
Planner agent node — produces a step-by-step execution plan from the user's request.

The planner uses an LCEL chain (prompt | llm | StrOutputParser) and does NOT
call any tools.  It resets ``iteration`` to 0 so the executor starts fresh.
"""
import logging

from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

import config
from graph.state import AgentState

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a meticulous task planner in a multi-agent AI system.

Analyse the user's request and produce a concise, numbered execution plan that the
Executor agent will follow step-by-step using available tools.

Available tool categories:
  Memory     : memory_get, memory_set, memory_delete, memory_list_keys
  Retrieval  : retrieval_search, retrieval_index, retrieval_delete
  HTTP       : http_get, http_post
  Scheduler  : schedule_create, schedule_list, schedule_remove
  Notification: notify_email, notify_slack, notify_console
  Auth       : auth_store_key, auth_get_key, auth_validate, auth_revoke

Output format — always a numbered list. Be specific about:
- Which tool each step uses
- What data it consumes from previous steps
- What condition triggers branching (e.g. if score < 0.1, escalate)

State assumptions before the plan if the request is ambiguous."""


def planner_node(state: AgentState) -> dict:
    """Generate a numbered execution plan from the conversation history.

    Args:
        state: Current AgentState.  ``state["messages"]`` must contain at least
               one HumanMessage with the user's task description.

    Returns:
        Partial AgentState update with:
          - messages: [AIMessage] containing the plan prefixed with "PLAN:\\n"
          - plan:     Raw plan text for the executor's reference
          - iteration: Reset to 0 for the upcoming executor pass
          - error:     Cleared to None
          - task_complete: Reset to False
    """
    llm = ChatOpenAI(model=config.LLM_MODEL, temperature=config.LLM_TEMPERATURE)
    prompt = ChatPromptTemplate.from_messages([
        ("system", _SYSTEM_PROMPT),
        ("placeholder", "{messages}"),
    ])
    chain = prompt | llm | StrOutputParser()

    try:
        plan_text = chain.invoke({"messages": state["messages"]})
    except Exception as exc:
        logger.error("planner_node failed: %s", exc)
        plan_text = f"PLANNING FAILED: {exc}"

    ai_msg = AIMessage(content=f"PLAN:\n{plan_text}")
    logger.debug("Plan produced:\n%s", plan_text)

    return {
        "messages": [ai_msg],
        "plan": plan_text,
        "iteration": 0,
        "error": None,
        "task_complete": False,
    }
