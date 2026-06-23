"""Direct PostgreSQL access for the edge (replaces the Supabase REST client).

The edge keeps a full local copy in SQLite (the source of truth for the
recognition/lookup path). This module reconciles that copy against the cloud
master PostgreSQL and pushes events. Every helper degrades gracefully:
``is_cloud_enabled()`` returns ``False`` when Postgres is disabled or
unconfigured, and the ``cursor()`` context manager yields ``None`` when a
connection cannot be opened — repositories check for ``None`` and fall back to
local-only operation instead of crashing.

Connections are short-lived (opened per operation) which is safe under Flask's
threaded server and more than fast enough for the edge's low write volume.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator, Optional

from config import CONFIG

logger = logging.getLogger(__name__)

# Lazily imported so the package only hard-requires psycopg when cloud sync is
# actually used. Cached as a module-level tuple ``(psycopg, dict_row)``.
_psycopg_cache = None


def is_cloud_enabled() -> bool:
    """True when Postgres is configured and not explicitly disabled."""
    if CONFIG.get("postgres_disabled"):
        return False
    return bool(
        CONFIG.get("postgres_host")
        and CONFIG.get("postgres_db")
        and CONFIG.get("postgres_user")
    )


def _conninfo() -> dict:
    """Build the psycopg keyword connection arguments from CONFIG."""
    kwargs = {
        "host": CONFIG["postgres_host"],
        "port": CONFIG["postgres_port"],
        "dbname": CONFIG["postgres_db"],
        "user": CONFIG["postgres_user"],
        "password": CONFIG["postgres_password"],
        "connect_timeout": CONFIG.get("postgres_connect_timeout", 5),
    }
    sslmode = CONFIG.get("postgres_sslmode")
    if sslmode:
        kwargs["sslmode"] = sslmode
    return kwargs


def _load_psycopg():
    """Import psycopg + dict_row once. Returns ``None`` when not installed."""
    global _psycopg_cache
    if _psycopg_cache is None:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError:
            logger.warning(
                "psycopg not installed; running in edge-only mode. "
                "Install with `pip install 'psycopg[binary]'` to enable cloud sync."
            )
            return None
        _psycopg_cache = (psycopg, dict_row)
    return _psycopg_cache


def get_connection():
    """Open a new PostgreSQL connection, or ``None`` for offline/edge-only mode."""
    if not is_cloud_enabled():
        logger.debug("PostgreSQL disabled — running in edge-only mode.")
        return None

    loaded = _load_psycopg()
    if loaded is None:
        return None
    psycopg, _ = loaded

    try:
        return psycopg.connect(**_conninfo())
    except Exception:  # noqa: BLE001 — never crash the app over connection issues
        logger.exception("Failed to connect to PostgreSQL; falling back to edge-only.")
        return None


@contextmanager
def cursor(commit: bool = False) -> Iterator[Optional["psycopg.Cursor"]]:  # type: ignore[name-defined]
    """Yield a dict-row cursor, or ``None`` when cloud is disabled/unreachable.

    Usage::

        with cursor(commit=True) as cur:
            if cur is None:
                return default
            cur.execute("SELECT ...", params)
            return cur.fetchall()

    On any exception inside the block the transaction is rolled back and the
    exception re-raised so call sites can log and degrade.
    """
    conn = get_connection()
    if conn is None:
        yield None
        return

    loaded = _load_psycopg()
    _, dict_row = loaded  # loaded can't be None here (get_connection succeeded)
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            try:
                yield cur
                if commit:
                    conn.commit()
            except Exception:
                conn.rollback()
                raise
    finally:
        conn.close()
