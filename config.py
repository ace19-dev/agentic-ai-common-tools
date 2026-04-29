"""
Central configuration module for the Agentic AI Project.

Import this module (``import config``) at the top of any file that needs
environment variables or the logging setup.  The side-effects on import are
intentional: dotenv is loaded once and the root logger is configured once,
so subsequent imports are no-ops.

All settings fall back to safe defaults so the project runs out-of-the-box
without a .env file (notifications go to console, encryption uses an ephemeral
key, etc.).
"""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── LLM ────────────────────────────────────────────────────────────────────────
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0"))

# ── Storage paths ───────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

AUTH_DB_PATH: str = os.getenv("AUTH_DB_PATH", str(DATA_DIR / "auth.db"))

# ── Memory MCP backend ──────────────────────────────────────────────────────────
MEMORY_DB_PATH: str = os.getenv("MEMORY_DB_PATH", str(DATA_DIR / "memory.db"))

# ── Retrieval MCP backend ───────────────────────────────────────────────────────
# Options: tfidf_sqlite (default) | vector | postgres
RETRIEVAL_BACKEND: str = os.getenv("RETRIEVAL_BACKEND", "tfidf_sqlite")
RETRIEVAL_DB_PATH: str = os.getenv("RETRIEVAL_DB_PATH", str(DATA_DIR / "retrieval.db"))
RETRIEVAL_VECTOR_PATH: str = os.getenv("RETRIEVAL_VECTOR_PATH", str(DATA_DIR / "vector_retrieval"))
RETRIEVAL_VECTOR_COLLECTION: str = os.getenv("RETRIEVAL_VECTOR_COLLECTION", "agent_retrieval")
RETRIEVAL_POSTGRES_DSN: str = os.getenv("RETRIEVAL_POSTGRES_DSN", "")
RETRIEVAL_POSTGRES_LANGUAGE: str = os.getenv("RETRIEVAL_POSTGRES_LANGUAGE", "english")

# ── Scheduler MCP backend ───────────────────────────────────────────────────────
# Options: apscheduler (default) | celery
SCHEDULER_BACKEND: str = os.getenv("SCHEDULER_BACKEND", "apscheduler")
SCHEDULER_DB_PATH: str = os.getenv("SCHEDULER_DB_PATH", str(DATA_DIR / "scheduler.db"))
SCHEDULER_CELERY_BROKER: str = os.getenv("SCHEDULER_CELERY_BROKER", "")
SCHEDULER_CELERY_RESULT_BACKEND: str = os.getenv("SCHEDULER_CELERY_RESULT_BACKEND", "")

# ── HTTP MCP ───────────────────────────────────────────────────────────────────
HTTP_TIMEOUT: int = int(os.getenv("HTTP_TIMEOUT_SECONDS", "10"))
HTTP_MAX_RETRIES: int = int(os.getenv("HTTP_MAX_RETRIES", "3"))

# ── Auth MCP ───────────────────────────────────────────────────────────────────
AUTH_FERNET_KEY: str = os.getenv("AUTH_FERNET_KEY", "")

# ── Notification MCP ──────────────────────────────────────────────────────────
SMTP_HOST: str = os.getenv("SMTP_HOST", "")
SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER: str = os.getenv("SMTP_USER", "")
SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM: str = os.getenv("SMTP_FROM", SMTP_USER)
SLACK_WEBHOOK_URL: str = os.getenv("SLACK_WEBHOOK_URL", "")
NOTIFICATION_DRY_RUN: bool = os.getenv("NOTIFICATION_DRY_RUN", "true").lower() == "true"

# ── Logging ─────────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
