"""HTTP client for Control iD facial collectors (iDFace / iDAccess family).

In Control iD mode the device performs face detection/recognition itself and
calls back into our Flask server (``POST /new_user_identified.fcgi``). This
client handles the *outbound* direction:

* ``login()`` — acquires a session token from ``POST /login.fcgi``.
* ``create_users()`` — pushes student records into the device's user table via
  ``POST /create_objects.fcgi`` so the device knows who to recognize.
* ``set_monitor()`` — points the device's online-mode callback at our server.

The device's HTTP API is FCGI + JSON. All methods degrade gracefully: they log
and return a falsy value on failure instead of raising, so a flaky device never
crashes the edge.

References: Control iD REST API (login.fcgi, create_objects.fcgi,
set_configuration.fcgi). Field names follow the documented ``users`` object.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Optional

from config import CONFIG

logger = logging.getLogger(__name__)


class ControlIdClient:
    def __init__(
        self,
        host: str | None = None,
        user: str | None = None,
        password: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.host = (host or CONFIG.get("control_id_host") or "").strip().rstrip("/")
        self.user = user or CONFIG.get("control_id_user") or ""
        self.password = password or CONFIG.get("control_id_password") or ""
        self.timeout = timeout or CONFIG.get("control_id_timeout_seconds", 10.0)
        self.session: Optional[str] = None

    # ------------------------------------------------------------------
    # Low-level HTTP
    # ------------------------------------------------------------------
    def is_configured(self) -> bool:
        return bool(self.host and self.user and self.password)

    def _base_url(self) -> str:
        host = self.host
        if not host.startswith(("http://", "https://")):
            host = f"http://{host}"
        return host

    def _post(self, path: str, payload: dict[str, Any], with_session: bool = True) -> Any:
        """POST JSON to ``path`` and return the parsed response (or None on error)."""
        url = f"{self._base_url()}/{path.lstrip('/')}"
        if with_session:
            if not self.session and not self.login():
                return None
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}session={self.session}"

        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8") or "{}"
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            logger.warning("Control iD %s returned HTTP %s: %s", path, exc.code, body)
            return None
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
            logger.warning("Control iD %s request failed: %s", path, exc)
            return None

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------
    def login(self) -> bool:
        """Acquire (or renew) a session token from the device."""
        if not self.is_configured():
            logger.info("Control iD not configured — skipping login.")
            return False
        result = self._post(
            "login.fcgi",
            {"login": self.user, "password": self.password},
            with_session=False,
        )
        if result and result.get("session"):
            self.session = result["session"]
            logger.info("Control iD session acquired (host=%s).", self.host)
            return True
        logger.warning("Control iD login failed (host=%s).", self.host)
        return False

    # ------------------------------------------------------------------
    # User provisioning (edge → device)
    # ------------------------------------------------------------------
    def create_users(self, users: list[dict[str, Any]]) -> bool:
        """Create/replace user records on the device.

        Each item should contain at least ``id`` (int — used as the user_id the
        device sends back) and ``name``. Extra documented fields (``registration``,
        ``password``, ``salt``) are passed through if present.
        """
        if not users:
            return True
        result = self._post(
            "create_objects.fcgi",
            {"object": "users", "values": users},
        )
        if result is None:
            logger.warning("Control iD create_users failed for %d users.", len(users))
            return False
        logger.info("Control iD: pushed %d users to device.", len(users))
        return True

    def sync_students(self, students: list[dict[str, Any]]) -> int:
        """Push a list of student dicts (from StudentRepository) to the device.

        Maps each student to a device user. The device ``user_id`` is taken from
        ``external_id`` (must be an integer) so the callback can be resolved back
        to the student. Students without a numeric ``external_id`` are skipped.

        Returns the number of users successfully pushed.
        """
        users: list[dict[str, Any]] = []
        skipped = 0
        for s in students:
            ext = s.get("external_id")
            try:
                device_id = int(ext)
            except (TypeError, ValueError):
                skipped += 1
                continue
            users.append({"id": device_id, "name": s.get("full_name") or ""})
        if skipped:
            logger.warning(
                "Control iD sync_students: %d students skipped (no numeric external_id).",
                skipped,
            )
        if not users:
            return 0
        return len(users) if self.create_users(users) else 0

    # ------------------------------------------------------------------
    # Online-mode callback configuration (device → edge)
    # ------------------------------------------------------------------
    def set_monitor(self, callback_host: str, callback_port: int, path: str = "/new_user_identified.fcgi") -> bool:
        """Point the device's online-mode monitor at our Flask server.

        Configures the device so that, after recognizing a face, it POSTs to
        ``http://<callback_host>:<callback_port><path>``.
        """
        result = self._post(
            "set_configuration.fcgi",
            {
                "monitor": {
                    "request_timeout": "5000",
                    "hostname": callback_host,
                    "port": str(callback_port),
                    "path": path,
                }
            },
        )
        if result is None:
            logger.warning("Control iD set_monitor failed.")
            return False
        logger.info(
            "Control iD monitor set → http://%s:%s%s", callback_host, callback_port, path
        )
        return True
