"""Device heartbeat repository — PostgreSQL master."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .postgres_client import cursor

logger = logging.getLogger(__name__)


class DeviceRepository:
    def __init__(self, device_id: str, school_id: str) -> None:
        self.device_id = device_id
        self.school_id = school_id

    def heartbeat(
        self,
        model_version: str | None = None,
        inference_provider: str | None = None,
        hardware_info: dict | None = None,
    ) -> bool:
        """Update ``devices.last_seen_at`` and optional runtime metadata.

        Best-effort: silently no-ops when cloud is off or on errors so the
        camera loop never blocks waiting on the network.
        """
        if not self.device_id:
            return False

        sets = ["last_seen_at = %(last_seen_at)s"]
        params: dict = {
            "last_seen_at": datetime.now(timezone.utc).isoformat(),
            "id": self.device_id,
        }
        if model_version is not None:
            sets.append("model_version = %(model_version)s")
            params["model_version"] = model_version
        if inference_provider is not None:
            sets.append("inference_provider = %(inference_provider)s")
            params["inference_provider"] = inference_provider
        if hardware_info is not None:
            sets.append("hardware_info = %(hardware_info)s")
            params["hardware_info"] = json.dumps(hardware_info)

        try:
            with cursor(commit=True) as cur:
                if cur is None:
                    return False
                cur.execute(
                    f"UPDATE devices SET {', '.join(sets)} WHERE id = %(id)s",
                    params,
                )
                return True
        except Exception:  # noqa: BLE001
            logger.exception("heartbeat failed for device=%s", self.device_id)
            return False
