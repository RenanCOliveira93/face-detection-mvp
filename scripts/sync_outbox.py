"""Flush local events to the configured PostgreSQL central database."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import CONFIG
from database import FaceDatabase
from sync_service import PostgresSink, flush_outbox


if __name__ == "__main__":
    if not CONFIG["postgres_dsn"]:
        raise SystemExit("POSTGRES_DSN não configurado; dados continuam na outbox local")
    print(flush_outbox(FaceDatabase(attendance_timezone=CONFIG["attendance_timezone"]), PostgresSink(CONFIG["postgres_dsn"])))
