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
# Options: tfidf_sqlite (default) | bm25_sqlite | vector | postgres
RETRIEVAL_BACKEND: str = os.getenv("RETRIEVAL_BACKEND", "tfidf_sqlite")
RETRIEVAL_DB_PATH: str = os.getenv("RETRIEVAL_DB_PATH", str(DATA_DIR / "retrieval.db"))
RETRIEVAL_BM25_DB_PATH: str = os.getenv("RETRIEVAL_BM25_DB_PATH", str(DATA_DIR / "retrieval_bm25.db"))
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

# ── Flight Monitor ──────────────────────────────────────────────────────────────
# Mode: "mock" (default) | "amadeus"
FLIGHT_API_MODE: str = os.getenv("FLIGHT_API_MODE", "mock")
# Amadeus for Developers — https://developers.amadeus.com
AMADEUS_CLIENT_ID: str = os.getenv("AMADEUS_CLIENT_ID", "")
AMADEUS_CLIENT_SECRET: str = os.getenv("AMADEUS_CLIENT_SECRET", "")
AMADEUS_BASE_URL: str = os.getenv("AMADEUS_BASE_URL", "https://test.api.amadeus.com")

# ── Notification MCP ──────────────────────────────────────────────────────────
SMTP_HOST: str = os.getenv("SMTP_HOST", "")
SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER: str = os.getenv("SMTP_USER", "")
SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM: str = os.getenv("SMTP_FROM", SMTP_USER)
SLACK_WEBHOOK_URL: str = os.getenv("SLACK_WEBHOOK_URL", "")
DISCORD_WEBHOOK_URL: str = os.getenv("DISCORD_WEBHOOK_URL", "")
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
TEAMS_WEBHOOK_URL: str = os.getenv("TEAMS_WEBHOOK_URL", "")
NOTIFICATION_DRY_RUN: bool = os.getenv("NOTIFICATION_DRY_RUN", "true").lower() == "true"

# ── Logging MCP backend ────────────────────────────────────────────────────────
# 백엔드 옵션: sqlite (기본값) | file | loki | elasticsearch
LOGGING_BACKEND: str = os.getenv("LOGGING_BACKEND", "sqlite")

# SQLite 백엔드
LOGGING_DB_PATH: str = os.getenv("LOGGING_DB_PATH", str(DATA_DIR / "agent_logs.db"))

# File 백엔드 (회전 JSON Lines)
LOGGING_FILE_PATH: str = os.getenv("LOGGING_FILE_PATH", str(DATA_DIR / "agent.log"))
LOGGING_FILE_MAX_BYTES: int = int(os.getenv("LOGGING_FILE_MAX_BYTES", str(10 * 1024 * 1024)))
LOGGING_FILE_BACKUP_COUNT: int = int(os.getenv("LOGGING_FILE_BACKUP_COUNT", "5"))

# Loki 백엔드 (Grafana Loki)
LOGGING_LOKI_URL: str = os.getenv("LOGGING_LOKI_URL", "http://localhost:3100")
LOGGING_LOKI_LABELS: str = os.getenv("LOGGING_LOKI_LABELS", '{"app": "agentic-ai"}')

# Elasticsearch / OpenSearch 백엔드
LOGGING_ES_URL: str = os.getenv("LOGGING_ES_URL", "http://localhost:9200")
LOGGING_ES_INDEX: str = os.getenv("LOGGING_ES_INDEX", "agentic-ai-logs")
LOGGING_ES_API_KEY: str = os.getenv("LOGGING_ES_API_KEY", "")

# Datadog 백엔드
LOGGING_DATADOG_API_KEY: str = os.getenv("LOGGING_DATADOG_API_KEY", "")
LOGGING_DATADOG_APP_KEY: str = os.getenv("LOGGING_DATADOG_APP_KEY", "")
LOGGING_DATADOG_SITE: str = os.getenv("LOGGING_DATADOG_SITE", "datadoghq.com")
LOGGING_DATADOG_SERVICE: str = os.getenv("LOGGING_DATADOG_SERVICE", "agentic-ai")
LOGGING_DATADOG_SOURCE: str = os.getenv("LOGGING_DATADOG_SOURCE", "python")

# PostgreSQL 백엔드
LOGGING_POSTGRES_DSN: str = os.getenv("LOGGING_POSTGRES_DSN", "")
LOGGING_POSTGRES_TABLE: str = os.getenv("LOGGING_POSTGRES_TABLE", "agent_logs")

# ── Python root logger ─────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
