"""
Customer Support Agent Example
================================
Scenario:
  A user submits a support question. The agent:
    1. Indexes FAQ documents into the retrieval store (idempotent — skips if already indexed).
    2. Searches the FAQ index for the best matching answer.
    3. Stores the question in memory under namespace 'support'.
    4. If a good match is found (score ≥ 0.1), returns the answer.
    5. If no match is found, escalates by posting to Slack #support-escalation.

Tools demonstrated:
  retrieval_index, retrieval_search, memory_set, notify_slack, notify_console

Usage:
  python -m examples.customer_support
  python -m examples.customer_support "How do I cancel my subscription?"

Environment:
  NOTIFICATION_DRY_RUN=true  (safe default — prints Slack messages to console)
"""
from __future__ import annotations

import json
import sys

from langchain_core.messages import HumanMessage

from graph.workflow import build_graph

FAQ_DOCUMENTS = [
    {
        "id": "faq-password",
        "content": "How do I reset my password? Visit Settings > Security > Reset Password. "
                   "A reset link will be emailed to your registered address.",
        "metadata": {"category": "account"},
    },
    {
        "id": "faq-payment",
        "content": "What payment methods do you accept? We accept Visa, Mastercard, "
                   "American Express, and PayPal. Cryptocurrency is not supported.",
        "metadata": {"category": "billing"},
    },
    {
        "id": "faq-cancel",
        "content": "How do I cancel my subscription? Navigate to Settings > Subscription > "
                   "Cancel Plan. Cancellation takes effect at the end of the billing period.",
        "metadata": {"category": "billing"},
    },
    {
        "id": "faq-refund",
        "content": "What is your refund policy? Full refunds are available within 30 days "
                   "of purchase. After 30 days, refunds are issued as account credits.",
        "metadata": {"category": "billing"},
    },
    {
        "id": "faq-contact",
        "content": "How do I contact support? Email support@example.com or use the live "
                   "chat widget in the bottom-right corner. Response time is under 24 hours.",
        "metadata": {"category": "contact"},
    },
    {
        "id": "faq-2fa",
        "content": "How do I enable two-factor authentication? Go to Settings > Security > "
                   "Two-Factor Authentication and follow the setup wizard.",
        "metadata": {"category": "account"},
    },
]

_TASK_TEMPLATE = """
You are a customer support agent. Execute these steps precisely:

Step 1 – Index FAQ documents (check first if already indexed to avoid duplicates):
  For each document below, call retrieval_index with its id, content, and metadata as JSON.
  Documents to index:
{faq_list}

Step 2 – Search for the user's question using retrieval_search:
  Query: "{question}"

Step 3 – Store the question in memory:
  Call memory_set with key="last_question", value="{question}", namespace="support"

Step 4 – Formulate a response:
  - If retrieval_search returns results with score ≥ 0.1, use the top result's content
    to answer the user's question in a friendly, helpful tone.
  - If no results have score ≥ 0.1, send a Slack escalation:
    Call notify_slack with channel="#support-escalation",
    message="[ESCALATION] Unanswered question: {question}"
    Then tell the user their question has been escalated to the support team.

Step 5 – Log completion:
  Call notify_console with level="INFO",
  message="Customer support query handled: {question}"
"""


def run(question: str = "What is your refund policy?") -> dict:
    """Run the customer-support multi-agent workflow for a single question.

    Idempotent: FAQ documents are indexed on every call, but the underlying
    retrieval store uses upsert so re-indexing is a no-op for unchanged docs.

    Args:
        question: The user's support question.

    Returns:
        Final AgentState dict including the full message history.
    """
    app = build_graph(scenario="customer_support")
    faq_list = "\n".join(
        f'  id={d["id"]!r}, content={d["content"]!r}, metadata={json.dumps(d["metadata"])!r}'
        for d in FAQ_DOCUMENTS
    )
    task = _TASK_TEMPLATE.format(
        faq_list=faq_list,
        question=question,
    )
    initial_state = {
        "messages": [HumanMessage(content=task)],
        "plan": None,
        "iteration": 0,
        "error": None,
        "task_complete": False,
        "scenario": "customer_support",
    }
    result = app.invoke(initial_state)
    _print_result(result)
    return result


def _print_result(result: dict) -> None:
    """Print a summary of the workflow result to stdout."""
    print("\n" + "=" * 60)
    print("CUSTOMER SUPPORT AGENT — RESULT")
    print("=" * 60)
    last_msg = result["messages"][-1]
    print(last_msg.content)
    print(f"\nIterations: {result.get('iteration')} | Complete: {result.get('task_complete')}")


if __name__ == "__main__":
    question = " ".join(sys.argv[1:]) or "What is your refund policy?"
    run(question)
