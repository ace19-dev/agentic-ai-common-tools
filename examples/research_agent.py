"""
Research Agent Example
======================
Scenario:
  Given a research topic and a list of URLs, the agent:
    1. Fetches the content of each URL via HTTP GET.
    2. Indexes each page as a document in the retrieval store.
    3. Searches the index for content relevant to the query.
    4. Composes a concise research summary from the top results.
    5. Stores the summary in memory under namespace 'research'.
    6. Emails the summary to the recipient (or dry-run prints it).

Tools demonstrated:
  http_get, retrieval_index, retrieval_search, memory_set, notify_email, notify_console

Usage:
  python -m examples.research_agent
  python -m examples.research_agent "Python async patterns"

Environment:
  NOTIFICATION_DRY_RUN=true  — prints the email to console instead of sending
  EMAIL_RECIPIENT             — override the default recipient address
"""
from __future__ import annotations

import os
import sys

from langchain_core.messages import HumanMessage

from graph.workflow import build_graph

DEFAULT_URLS = [
    "https://httpbin.org/json",
    "https://httpbin.org/html",
    "https://httpbin.org/robots.txt",
]

_TASK_TEMPLATE = """
You are a research agent. Execute the following steps in order:

Step 1 – Fetch content from each URL using http_get:
{url_list}

Step 2 – Index each fetched page as a document using retrieval_index:
  - Use the URL as the doc_id.
  - Use the response body as the content.
  - Set metadata to {{"source": "<url>", "status_code": <status_code>}}.
  - Skip pages where ok=false or body is empty.

Step 3 – Search the index for content related to the query using retrieval_search:
  Query: "{query}"
  top_k: 5

Step 4 – Compose a research summary:
  Write 3–5 sentences summarising the most relevant findings from the search results.
  Include which sources contributed to each finding.

Step 5 – Store the summary in memory:
  Call memory_set with key="research_summary", namespace="research",
  value=<your summary text>

Step 6 – Send the summary via email using notify_email:
  to: "{recipient}"
  subject: "Research Report: {query}"
  body: <your summary from Step 4>

Step 7 – Log completion:
  Call notify_console with level="INFO", message="Research complete for query: {query}"
"""


def run(query: str = "HTTP response formats",
        urls: list[str] | None = None,
        recipient: str = "") -> dict:
    """Fetch, index, search, and email a research summary.

    Args:
        query:     Natural language research query used for retrieval search.
        urls:      Pages to fetch and index; defaults to DEFAULT_URLS.
        recipient: Email address for the summary report.
                   Falls back to EMAIL_RECIPIENT env var, then a placeholder.

    Returns:
        Final AgentState dict.
    """
    urls = urls or DEFAULT_URLS
    recipient = recipient or os.getenv("EMAIL_RECIPIENT", "researcher@example.com")
    app = build_graph(scenario="research")
    url_list = "\n".join(f"  - {u}" for u in urls)
    task = _TASK_TEMPLATE.format(
        url_list=url_list,
        query=query,
        recipient=recipient,
    )
    initial_state = {
        "messages": [HumanMessage(content=task)],
        "plan": None,
        "iteration": 0,
        "error": None,
        "task_complete": False,
        "scenario": "research",
    }
    result = app.invoke(initial_state)
    _print_result(result)
    return result


def _print_result(result: dict) -> None:
    """Print a summary of the workflow result to stdout."""
    print("\n" + "=" * 60)
    print("RESEARCH AGENT — RESULT")
    print("=" * 60)
    last_msg = result["messages"][-1]
    print(last_msg.content)
    print(f"\nIterations: {result.get('iteration')} | Complete: {result.get('task_complete')}")


if __name__ == "__main__":
    query = " ".join(sys.argv[1:]) or "HTTP response formats"
    run(query)
