"""Best-effort synchronization from authoritative local SQLite to central PostgreSQL."""

from __future__ import annotations

import json
from collections.abc import Callable

from database import FaceDatabase


def flush_outbox(db: FaceDatabase, sink: Callable[[str, str, dict], None], limit: int = 100) -> dict:
    """Deliver pending items. The central sink must upsert by event_key."""
    sent = failed = 0
    for item in db.pending_outbox(limit):
        try:
            sink(item["event_key"], item["aggregate_type"], json.loads(item["payload_json"]))
        except Exception as exc:
            db.mark_outbox(item["id"], False, str(exc)[:500])
            failed += 1
        else:
            db.mark_outbox(item["id"], True)
            sent += 1
    return {"sent": sent, "failed": failed}


class PostgresSink:
    """Tiny adapter; psycopg is imported only when PostgreSQL mode is used."""

    def __init__(self, dsn: str):
        self.dsn = dsn

    def __call__(self, event_key: str, aggregate_type: str, payload: dict) -> None:
        import psycopg

        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS school_sync_events (
                       event_key TEXT PRIMARY KEY, aggregate_type TEXT NOT NULL,
                       payload JSONB NOT NULL, received_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
            )
            conn.execute(
                """INSERT INTO school_sync_events(event_key,aggregate_type,payload)
                   VALUES (%s,%s,%s) ON CONFLICT(event_key) DO NOTHING""",
                (event_key, aggregate_type, json.dumps(payload)),
            )
