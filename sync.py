"""Edge sync orchestration.

* ``OutboxWorker`` — background thread that drains the SQLite ``outbox`` into
  the PostgreSQL master with exponential backoff. Single instance per process.
* ``reconcile_students`` — pulls active students (+ current embeddings, unless
  in Control iD mode) from the PostgreSQL master into the local SQLite cache at
  boot. No-ops when offline.
* ``HeartbeatWorker`` — periodically pings ``devices.last_seen_at`` so the
  cloud panel knows the edge is alive.

All three degrade gracefully: if cloud is disabled or unreachable they simply
stop trying for that cycle and the camera loop is never blocked.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, time as dtime
from typing import Optional
from zoneinfo import ZoneInfo

from config import CONFIG
from database import FaceDatabase
from repositories import (
    DeviceRepository,
    DietaryRestrictionRepository,
    KitchenRepository,
    PresenceEventRepository,
    StudentRepository,
    is_cloud_enabled,
)

logger = logging.getLogger(__name__)

MAX_OUTBOX_ATTEMPTS = 10


def _backoff_seconds(attempts: int) -> float:
    """Exponential backoff capped at 5 minutes."""
    return min(300.0, 2.0 ** min(attempts, 8))


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------
def reconcile_students(
    db: FaceDatabase,
    school_id: str,
    model_version: str,
    require_embedding: bool = True,
) -> int:
    """Pull students (+ current embeddings) from the PostgreSQL master into cache.

    When ``require_embedding`` is False (Control iD mode), students are pulled
    without embeddings — the device handles recognition, so the edge only needs
    the metadata to record events and notify guardians.

    Returns how many students were synced. Logs but never raises on failure.
    """
    if not is_cloud_enabled() or not school_id:
        logger.info("reconcile_students skipped (cloud disabled or no school_id).")
        return 0

    repo = StudentRepository(school_id=school_id, model_version=model_version)
    if require_embedding:
        students = repo.fetch_active_with_embeddings()
    else:
        students = repo.fetch_active()
    if not students:
        logger.info("reconcile_students: nothing returned from PostgreSQL.")
        return 0

    synced = 0
    for student in students:
        face_id = student.get("face_id")
        if not face_id:
            continue
        try:
            db.upsert_face_from_remote(
                face_id=face_id,
                supabase_id=student["id"],
                full_name=student["full_name"],
                phone=student.get("phone"),
                email=student.get("email"),
                encoding=student.get("embedding"),
                class_id=student.get("class_id"),
                class_name=student.get("class_name"),
                class_grade=student.get("class_grade"),
                class_shift=student.get("class_shift"),
                enrollment_number=student.get("enrollment_number"),
            )
            synced += 1
        except Exception:  # noqa: BLE001
            logger.exception("reconcile_students: failed to upsert face_id=%s", face_id)

    logger.info("reconcile_students: synced %d students from Supabase.", synced)
    return synced


# ---------------------------------------------------------------------------
# Outbox worker
# ---------------------------------------------------------------------------
class OutboxWorker:
    """Drains queued sync operations from SQLite into Supabase."""

    def __init__(self, db: FaceDatabase, school_id: str, device_id: Optional[str]) -> None:
        self.db = db
        self.school_id = school_id
        self.device_id = device_id
        self.events_repo = PresenceEventRepository(school_id=school_id, device_id=device_id)
        self.poll_interval = float(CONFIG.get("outbox_poll_seconds", 5.0))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        if not is_cloud_enabled() or not self.school_id:
            logger.info("OutboxWorker not started: cloud disabled or no school_id.")
            return
        self._thread = threading.Thread(
            target=self._run, name="outbox-worker", daemon=True
        )
        self._thread.start()
        logger.info("OutboxWorker started (poll=%.1fs).", self.poll_interval)

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._drain_once()
            except Exception:  # noqa: BLE001
                logger.exception("OutboxWorker cycle failed; will retry.")
            self._stop.wait(self.poll_interval)

    def _drain_once(self) -> None:
        pending = self.db.fetch_pending_outbox(limit=25)
        if not pending:
            return
        logger.debug("OutboxWorker: %d pending items.", len(pending))
        for item in pending:
            self._handle(item)

    def _handle(self, item: dict) -> None:
        kind = item["kind"]
        payload = item["payload"]
        attempts = item["attempts"]

        if attempts >= MAX_OUTBOX_ATTEMPTS:
            self.db.mark_outbox_failed(
                item["id"],
                error=f"giving up after {attempts} attempts",
                retry_in_seconds=None,
            )
            logger.warning(
                "OutboxWorker: dropping item id=%s after %d attempts.",
                item["id"],
                attempts,
            )
            return

        try:
            ok = self._dispatch(kind, payload)
        except Exception as exc:  # noqa: BLE001
            self.db.mark_outbox_failed(
                item["id"], error=str(exc), retry_in_seconds=_backoff_seconds(attempts + 1)
            )
            logger.exception("OutboxWorker: dispatch raised for id=%s", item["id"])
            return

        if ok:
            self.db.mark_outbox_sent(item["id"])
        else:
            self.db.mark_outbox_failed(
                item["id"],
                error="dispatch returned false",
                retry_in_seconds=_backoff_seconds(attempts + 1),
            )

    def _dispatch(self, kind: str, payload: dict) -> bool:
        if kind == "presence_event":
            return self.events_repo.push(payload)
        logger.warning("OutboxWorker: unknown kind=%r; marking failed.", kind)
        return False


# ---------------------------------------------------------------------------
# Heartbeat worker
# ---------------------------------------------------------------------------
class HeartbeatWorker:
    def __init__(
        self,
        device_id: str,
        school_id: str,
        model_version: str,
        inference_provider: str,
    ) -> None:
        self.repo = DeviceRepository(device_id=device_id, school_id=school_id)
        self.model_version = model_version
        self.inference_provider = inference_provider
        self.interval = float(CONFIG.get("heartbeat_seconds", 30.0))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        if not is_cloud_enabled() or not self.repo.device_id:
            logger.info("HeartbeatWorker not started: cloud disabled or no device_id.")
            return
        self._thread = threading.Thread(
            target=self._run, name="heartbeat-worker", daemon=True
        )
        self._thread.start()
        logger.info("HeartbeatWorker started (interval=%.1fs).", self.interval)

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        # Immediate first heartbeat at boot so the cloud panel sees the device fast.
        self.repo.heartbeat(
            model_version=self.model_version,
            inference_provider=self.inference_provider,
        )
        while not self._stop.wait(self.interval):
            self.repo.heartbeat(
                model_version=self.model_version,
                inference_provider=self.inference_provider,
            )


# ---------------------------------------------------------------------------
# Kitchen dispatcher
# ---------------------------------------------------------------------------
def _build_kitchen_message(
    template: str,
    school_name: str,
    shift: str,
    present_by_class: list[dict],
    students_with_restrictions: list[dict],
    shift_filter: list[str] | None = None,
) -> tuple[str, int, int]:
    """Render the kitchen template + return ``(message, present_count, restrictions_count)``.

    ``present_by_class`` comes from ``FaceDatabase.count_present_by_class()``.
    ``students_with_restrictions`` from the cloud view (PostgreSQL) when
    available, or an empty list when offline.
    """

    def shift_matches(s: str | None) -> bool:
        if not shift_filter:
            return True
        if s is None:
            return False
        return s in shift_filter

    relevant_classes = [c for c in present_by_class if shift_matches(c.get("class_shift"))]
    present_total = sum(c["present_count"] for c in relevant_classes)

    # Build per-class breakdown line
    breakdown_parts: list[str] = []
    for c in relevant_classes:
        grade = c.get("class_grade") or "—"
        name = c.get("class_name") or "—"
        breakdown_parts.append(f"{grade} {name}: {c['present_count']}")
    breakdown = "; ".join(breakdown_parts) if breakdown_parts else "sem turmas com presença"

    # Restrictions: only those whose student is in a relevant shift
    relevant_restrictions = [
        s for s in students_with_restrictions
        if shift_matches(s.get("shift")) and s.get("restrictions")
    ]
    if relevant_restrictions:
        restriction_lines = []
        for s in relevant_restrictions:
            names = ", ".join(r["name"] for r in s["restrictions"])
            restriction_lines.append(f"- {s['full_name']} ({names})")
        restrictions_summary = (
            f"⚠️ {len(relevant_restrictions)} aluno(s) com restrição alimentar:\n"
            + "\n".join(restriction_lines)
        )
    else:
        restrictions_summary = "Nenhum aluno presente com restrição alimentar registrada."

    rendered = (
        template
        .replace("{{school_name}}", school_name)
        .replace("{{shift}}", shift)
        .replace("{{present_total}}", str(present_total))
        .replace("{{class_breakdown}}", breakdown)
        .replace("{{restrictions_summary}}", restrictions_summary)
    )
    return rendered, present_total, len(relevant_restrictions)


class KitchenDispatchWorker:
    """Schedules + executes the daily kitchen notification.

    Checks every minute against ``school_settings.kitchen_dispatch_time`` in the
    school's timezone. When the wall clock crosses the target time, the worker
    queries the current presence + dietary restrictions and pushes the message
    to every active kitchen recipient. ``(business_date, shift)`` is used as
    the idempotency key — no duplicate sends per day.
    """

    def __init__(
        self,
        db: FaceDatabase,
        school_id: str,
        send_message_fn,
    ) -> None:
        self.db = db
        self.school_id = school_id
        self.send = send_message_fn
        self.kitchen_repo = KitchenRepository(school_id=school_id)
        self.dietary_repo = DietaryRestrictionRepository(school_id=school_id)
        self.tz = ZoneInfo(CONFIG.get("attendance_timezone") or "UTC")
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_check_minute: str | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        if not is_cloud_enabled() or not self.school_id:
            logger.info("KitchenDispatchWorker not started: cloud disabled or no school_id.")
            return
        self._thread = threading.Thread(
            target=self._run, name="kitchen-worker", daemon=True
        )
        self._thread.start()
        logger.info("KitchenDispatchWorker started (checks every 60s).")

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:  # noqa: BLE001
                logger.exception("KitchenDispatchWorker tick failed.")
            self._stop.wait(60)

    def _tick(self) -> None:
        settings = self.kitchen_repo.get_settings()
        if not settings or not settings.get("kitchen_enabled"):
            return
        dispatch_time = settings.get("kitchen_dispatch_time")
        if not dispatch_time:
            return

        now_local = datetime.now(self.tz)
        try:
            target = dtime.fromisoformat(dispatch_time)
        except (TypeError, ValueError):
            logger.warning("Invalid kitchen_dispatch_time=%r", dispatch_time)
            return

        # Trigger if we're within the same minute as the target (avoids missing
        # the instant due to the 60s polling cadence).
        if not (
            now_local.hour == target.hour and now_local.minute == target.minute
        ):
            return

        shifts = settings.get("kitchen_dispatch_shifts") or ["manha"]
        if not isinstance(shifts, list):
            shifts = [shifts]

        for shift in shifts:
            self._dispatch(settings, shift, "schedule")

    def dispatch_now(self, shift: str = "manha") -> dict:
        """Manual trigger — same idempotency rules apply.

        Returns a dict describing the outcome (sent vs skipped) for callers
        (Flask endpoint, CLI)."""
        settings = self.kitchen_repo.get_settings() or {}
        return self._dispatch(settings, shift, "manual")

    def _dispatch(
        self,
        settings: dict,
        shift: str,
        triggered_by: str,
    ) -> dict:
        business_date = datetime.now(self.tz).date().isoformat()

        if not self.db.try_record_kitchen_dispatch(business_date, shift, triggered_by):
            logger.info(
                "Kitchen dispatch already done for %s/%s — skipping.",
                business_date,
                shift,
            )
            return {
                "status": "skipped",
                "reason": "already dispatched today",
                "business_date": business_date,
                "shift": shift,
            }

        recipients = self.kitchen_repo.list_active_recipients()
        if not recipients:
            logger.warning("No active kitchen recipients for school=%s.", self.school_id)
            self.db.finalize_kitchen_dispatch(business_date, shift, 0, 0, 0, "")
            return {
                "status": "skipped",
                "reason": "no recipients",
                "business_date": business_date,
                "shift": shift,
            }

        school = self.kitchen_repo.get_school() or {}
        template = settings.get("kitchen_message_template") or (
            "Cozinha {{school_name}} — Hoje há {{present_total}} alunos "
            "presentes ({{shift}}). {{restrictions_summary}}"
        )

        present_by_class = self.db.count_present_by_class()
        students_with_restrictions = self.dietary_repo.students_present_today_with_restrictions()

        message, present_count, restrictions_count = _build_kitchen_message(
            template=template,
            school_name=school.get("name") or "Escola",
            shift=shift,
            present_by_class=present_by_class,
            students_with_restrictions=students_with_restrictions,
            shift_filter=[shift],
        )

        # Send to every recipient. Per-recipient send failures are logged but
        # don't abort the rest.
        delivered = 0
        for recipient in recipients:
            phone = recipient.get("phone_e164")
            if not phone:
                continue
            try:
                ok, info = self.send(phone, message)
                if ok:
                    delivered += 1
                else:
                    logger.warning(
                        "Kitchen send failed to %s: %s", phone, info
                    )
            except Exception:  # noqa: BLE001
                logger.exception("Kitchen send raised for %s", phone)

        self.db.finalize_kitchen_dispatch(
            business_date=business_date,
            shift=shift,
            recipients_count=delivered,
            present_count=present_count,
            restrictions_count=restrictions_count,
            message_body=message,
        )
        logger.info(
            "Kitchen dispatch done: shift=%s recipients=%d/%d present=%d restrictions=%d",
            shift,
            delivered,
            len(recipients),
            present_count,
            restrictions_count,
        )
        return {
            "status": "sent",
            "business_date": business_date,
            "shift": shift,
            "recipients_count": delivered,
            "present_count": present_count,
            "restrictions_count": restrictions_count,
            "message": message,
        }
