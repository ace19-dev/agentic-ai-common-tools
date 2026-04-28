"""
Agentic AI Project — Entry Point
==================================
Usage:
  python main.py                                        # default demo task
  python main.py --example customer_support
  python main.py --example customer_support "What is the refund policy?"
  python main.py --example research "Python async"
  python main.py --example monitoring
  python main.py "Store my name Alice and send a Slack notification to #general"
"""
import argparse
import sys

import config  # triggers dotenv load + logging setup
from graph.workflow import build_graph
from langchain_core.messages import HumanMessage


def run_custom(task: str) -> None:
    """Run the general-purpose multi-agent workflow with an arbitrary task string.

    Prints each message from the conversation history, role-labelled and
    truncated to 600 chars, followed by a summary line.

    Args:
        task: Free-form task description passed as the first HumanMessage.
    """
    app = build_graph()
    initial_state = {
        "messages": [HumanMessage(content=task)],
        "plan": None,
        "iteration": 0,
        "error": None,
        "task_complete": False,
        "scenario": None,
    }
    result = app.invoke(initial_state)
    print("\n" + "=" * 60)
    for msg in result["messages"]:
        role = type(msg).__name__.replace("Message", "")
        print(f"[{role}] {msg.content[:600]}")
    print(f"\nComplete: {result.get('task_complete')} | Iterations: {result.get('iteration')}")


def main() -> None:
    """CLI entry point — dispatches to the selected example or a custom task."""
    parser = argparse.ArgumentParser(
        description="Multi-agent AI framework with composable tools and MCPs"
    )
    parser.add_argument(
        "--example",
        choices=["customer_support", "research", "monitoring"],
        help="Run a pre-built example scenario",
    )
    parser.add_argument(
        "task",
        nargs="*",
        help="Custom task description (used when --example is not provided)",
    )
    args = parser.parse_args()

    if args.example == "customer_support":
        from examples.customer_support import run
        question = " ".join(args.task) if args.task else "What is your refund policy?"
        run(question)

    elif args.example == "research":
        from examples.research_agent import run
        query = " ".join(args.task) if args.task else "HTTP response formats"
        run(query)

    elif args.example == "monitoring":
        from examples.monitoring_agent import run
        targets = args.task if args.task else None
        run(targets)

    else:
        task = " ".join(args.task) if args.task else (
            "Remember my name is Alice and send a Slack notification to #general saying 'Alice is online'"
        )
        run_custom(task)


if __name__ == "__main__":
    main()
