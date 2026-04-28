"""
Auth MCP — Fernet-encrypted API key vault backed by SQLite.

Encryption key lifecycle:
  - If AUTH_FERNET_KEY is set in .env, keys survive process restarts.
  - If not set, an ephemeral key is generated per process and a WARNING is
    logged — previously stored tokens become unreadable after restart.

The ciphertext column is BLOB because Fernet.encrypt() returns bytes, not str.
sqlite3 stores Python bytes values as BLOB automatically.
"""
import logging
import os
import sqlite3
import time
from typing import Optional

import config
from core.base_mcp import BaseMCP, MCPResult

logger = logging.getLogger(__name__)


class AuthMCP(BaseMCP):
    """Fernet-encrypted API key vault backed by SQLite.

    Each stored key is symmetrically encrypted before persistence. If
    AUTH_FERNET_KEY is not set, an ephemeral key is generated per process run —
    all previously stored tokens become unreadable. Set AUTH_FERNET_KEY in .env
    for durable storage.
    """

    def __init__(self, db_path: str = "data/auth.db",
                 fernet_key: Optional[bytes] = None):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._fernet = self._init_fernet(fernet_key)
        self._init_db()

    # ── Fernet setup ──────────────────────────────────────────────────────────

    def _init_fernet(self, key: Optional[bytes]):
        try:
            from cryptography.fernet import Fernet
            raw = key or config.AUTH_FERNET_KEY
            if raw:
                return Fernet(raw.encode() if isinstance(raw, str) else raw)
            ephemeral = Fernet.generate_key()
            logger.warning(
                "AUTH_FERNET_KEY not set — using ephemeral key. "
                "Add AUTH_FERNET_KEY=%s to .env to persist tokens across restarts.",
                ephemeral.decode(),
            )
            return Fernet(ephemeral)
        except ImportError:
            logger.error("cryptography not installed — run: pip install cryptography")
            return None

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tokens (
                    service    TEXT PRIMARY KEY,
                    ciphertext BLOB NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            conn.commit()

    # ── Public API ────────────────────────────────────────────────────────────

    def store(self, service: str, key: str) -> MCPResult:
        if not self._fernet:
            return MCPResult.fail("Encryption unavailable — install cryptography package")
        try:
            ciphertext = self._fernet.encrypt(key.encode("utf-8"))
            now = time.time()
            with self._connect() as conn:
                conn.execute("""
                    INSERT INTO tokens (service, ciphertext, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(service) DO UPDATE SET
                        ciphertext = excluded.ciphertext,
                        updated_at = excluded.updated_at
                """, (service, ciphertext, now, now))
                conn.commit()
            return MCPResult.ok(data="stored")
        except Exception as exc:
            logger.error("auth.store failed: %s", exc)
            return MCPResult.fail(str(exc))

    def retrieve(self, service: str) -> MCPResult:
        if not self._fernet:
            return MCPResult.fail("Encryption unavailable")
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT ciphertext FROM tokens WHERE service=?", (service,)
                ).fetchone()
            if not row:
                return MCPResult.fail(f"No key stored for service '{service}'")
            # sqlite3 returns BLOB columns as memoryview on some Python versions;
            # bytes() converts both bytes and memoryview to a plain bytes object.
            plaintext = self._fernet.decrypt(bytes(row["ciphertext"])).decode("utf-8")
            return MCPResult.ok(data=plaintext)
        except Exception as exc:
            logger.error("auth.retrieve failed for '%s': %s", service, exc)
            return MCPResult.fail(f"Decryption failed for '{service}': {exc}")

    def validate(self, service: str) -> MCPResult:
        result = self.retrieve(service)
        return MCPResult.ok(data=result.success)

    def revoke(self, service: str) -> MCPResult:
        try:
            with self._connect() as conn:
                cursor = conn.execute("DELETE FROM tokens WHERE service=?", (service,))
                conn.commit()
            if cursor.rowcount == 0:
                return MCPResult.fail(f"No key found for service '{service}'")
            return MCPResult.ok(data="revoked")
        except Exception as exc:
            logger.error("auth.revoke failed: %s", exc)
            return MCPResult.fail(str(exc))

    def health_check(self) -> MCPResult:
        try:
            with self._connect() as conn:
                count = conn.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
            return MCPResult.ok(data={
                "mcp": "auth",
                "encryption": "fernet" if self._fernet else "unavailable",
                "stored_services": count,
            })
        except Exception as exc:
            return MCPResult.fail(str(exc))


# ── Module-level singleton ────────────────────────────────────────────────────

_instance: Optional[AuthMCP] = None


def get_auth_mcp() -> AuthMCP:
    """Return the process-wide AuthMCP singleton, creating it on first call."""
    global _instance
    if _instance is None:
        _instance = AuthMCP(db_path=config.AUTH_DB_PATH)
    return _instance
