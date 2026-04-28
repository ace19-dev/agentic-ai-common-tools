"""Pre-built example scenarios demonstrating the multi-agent framework."""
from .customer_support import run as run_customer_support
from .monitoring_agent import run as run_monitoring_agent
from .research_agent import run as run_research_agent

__all__ = ["run_customer_support", "run_research_agent", "run_monitoring_agent"]
