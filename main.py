"""Aplicação Flask com reconhecimento facial por similaridade usando uma foto por aluno."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

import cv2
import face_recognition
from flask import Flask, Response, jsonify, render_template

from config import CONFIG
from database import FaceDatabase
from face_registry import FaceRegistry
from integrations.webhook_client import publish_presence_event
from messaging import send_whatsapp_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
db = FaceDatabase()
registry = FaceRegistry(db)

PRESENCE_EXIT_TIMEOUT_SECONDS = 8
MAX_LIVE_EVENTS = 200

state = {
    "status": "idle",
    "latest_person": None,
    "latest_match_score": None,
    "last_detection": None,
    "frame_info": [],
    "last_event_direction": None,
    "last_event_at": None,
    "last_message_sent": False,
    "last_message_info": None,
    "recent_people": {},
}
state_lock = threading.Lock()
active_presence_tracks: dict[str, dict] = {}
live_events: list[dict] = []


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_live_event(event: dict) -> None:
    with state_lock:
        live_events.insert(0, event)
        del live_events[MAX_LIVE_EVENTS:]
        state["last_event_direction"] = event["direction"]
        state["last_event_at"] = event["event_at"]


def _record_presence_event(person: dict, direction: str, match_score: float | None) -> dict:
    event_id = db.create_presence_event(
        face_id=person["id"],
        direction=direction,
        match_score=match_score,
    )
    events = db.get_presence_events(limit=1)
    event = events[0] if events else {
        "id": event_id,
        "face_id": person["id"],
        "full_name": person["full_name"],
        "phone": person.get("notification_phone") or person.get("phone"),
        "direction": direction,
        "event_at": _iso_now(),
        "match_score": match_score,
        "message_ok": None,
        "message_info": None,
        "message_sent_at": None,
        "webhook_ok": None,
        "webhook_status": None,
        "webhook_info": None,
        "webhook_sent_at": None,
    }
    _append_live_event(event)
    return event


def _notify_presence_event_async(event: dict, person: dict, match_score: float | None) -> None:
    direction = event["direction"]
    if direction == "entrada":
        message = f"Aluno {person['full_name']} chegou na escola."
    else:
        message = f"Aluno {person['full_name']} saiu da escola."

    recipient = db.get_preferred_notification_recipient(person["id"], channel="whatsapp")
    target_phone = (recipient or {}).get("phone") or person.get("phone", "")
    success, info = send_whatsapp_message(target_phone, message)
    db.update_presence_event_message(event["id"], message_ok=success, message_info=str(info))
    db.log_detection(person["id"], similarity=match_score, message_ok=success, message_info=str(info))

    with state_lock:
        state["last_message_sent"] = success
        state["last_message_info"] = str(info)
        state["recent_people"][person["id"]] = {
            "full_name": person["full_name"],
            "phone": target_phone,
            "last_direction": direction,
            "message_sent": success,
            "message_info": str(info),
            "event_at": event["event_at"],
        }


def _publish_presence_webhook(event: dict, person: dict) -> None:
    try:
        webhook_result = publish_presence_event(event=event, person=person, source="camera")
        db.update_presence_event_webhook(
            event_id=event["id"],
            webhook_ok=bool(webhook_result.get("ok")),
            webhook_status=webhook_result.get("status"),
            webhook_info=str(webhook_result.get("info", "")),
            webhook_sent_at=webhook_result.get("sent_at"),
        )
    except Exception as exc:  # proteção para não afetar loop de câmera
        logger.exception(
            "presence_webhook_unhandled_error",
            extra={
                "event_id": event.get("id"),
                "face_id": event.get("face_id"),
                "error": str(exc),
            },
        )
        db.update_presence_event_webhook(
            event_id=event["id"],
            webhook_ok=False,
            webhook_status=None,
            webhook_info=f"Erro inesperado webhook: {exc}",
        )


def _start_event_notification(person: dict, direction: str, match_score: float | None) -> None:
    cooldown_by_direction = {
        "entrada": CONFIG["entry_cooldown_seconds"],
        "saida": CONFIG["exit_cooldown_seconds"],
    }
    allowed, lock_reason = db.try_reserve_message_dispatch(
        face_id=person["id"],
        direction=direction,
        cooldown_seconds=cooldown_by_direction.get(direction, CONFIG["entry_cooldown_seconds"]),
    )

    if not allowed:
        event = _record_presence_event(person, direction, match_score)
        info = lock_reason
        db.update_presence_event_message(event["id"], message_ok=False, message_info=info)
        with state_lock:
            state["last_message_sent"] = False
            state["last_message_info"] = info
        return

    event = _record_presence_event(person, direction, match_score)
    _publish_presence_webhook(event, person)
    threading.Thread(
        target=_notify_presence_event_async,
        args=(event, person, match_score),
        daemon=True,
    ).start()


def _expire_presence_tracks(now: float) -> None:
    to_close = []
    with state_lock:
        for face_id, track in active_presence_tracks.items():
            if now - track["last_seen"] > PRESENCE_EXIT_TIMEOUT_SECONDS:
                to_close.append(track["person"])
        for person in to_close:
            active_presence_tracks.pop(person["id"], None)

    for person in to_close:
        _start_event_notification(person, "saida", None)


def generate_frames():
    cap = cv2.VideoCapture(CONFIG["camera_index"])
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    frame_count = 0
    scale = CONFIG["frame_process_scale"]
    process_every = max(1, int(CONFIG["frame_process_every"]))

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frame_count += 1
        current_faces = []

        if frame_count % process_every == 0:
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            small = cv2.resize(rgb_frame, (0, 0), fx=scale, fy=scale)
            locations = face_recognition.face_locations(small, model="hog")
            encodings = face_recognition.face_encodings(small, locations)

            for encoding, location in zip(encodings, locations):
                person, match_score = registry.match_encoding(encoding)
                top, right, bottom, left = [int(v / scale) for v in location]
                current_faces.append(
                    {
                        "location": (top, right, bottom, left),
                        "known": bool(person),
                        "person": person,
                        "match_score": match_score,
                    }
                )
                if person:
                    _handle_recognized(person, match_score)
                else:
                    with state_lock:
                        state.update(
                            {
                                "status": "unknown",
                                "latest_person": None,
                                "latest_match_score": match_score,
                                "last_message_sent": False,
                                "last_message_info": None,
                            }
                        )
            with state_lock:
                state["frame_info"] = current_faces
                if not current_faces:
                    state.update(
                        {
                            "status": "idle",
                            "latest_person": None,
                            "latest_match_score": None,
                            "last_message_sent": False,
                            "last_message_info": None,
                        }
                    )

            _expire_presence_tracks(time.time())

        with state_lock:
            faces_to_draw = list(state["frame_info"])

        for face in faces_to_draw:
            top, right, bottom, left = face["location"]
            known = face["known"] and face["person"]
            color = (0, 180, 0) if known else (0, 0, 220)
            label = face["person"]["full_name"] if known else "NAO RECONHECIDO"
            cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
            cv2.rectangle(frame, (left, bottom - 28), (right, bottom), color, cv2.FILLED)
            cv2.putText(frame, label, (left + 6, bottom - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)

        ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ret:
            continue
        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

    cap.release()


def _handle_recognized(person: dict, match_score: float | None):
    now = time.time()
    with state_lock:
        state.update(
            {
                "status": "recognized",
                "latest_person": person,
                "latest_match_score": match_score,
                "last_detection": now,
            }
        )

        existing_track = active_presence_tracks.get(person["id"])
        if not existing_track:
            active_presence_tracks[person["id"]] = {"person": person, "last_seen": now}
            should_record_entry = True
        else:
            existing_track["last_seen"] = now
            should_record_entry = False

    if should_record_entry:
        _start_event_notification(person, "entrada", match_score)


@app.route("/")
def index():
    return render_template("index.html", faces=db.list_faces(), config=CONFIG)


@app.route("/video_feed")
def video_feed():
    return Response(generate_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/api/status")
def api_status():
    with state_lock:
        return jsonify(
            {
                "status": state["status"],
                "person": state["latest_person"],
                "message_sent": state["last_message_sent"],
                "message_info": state["last_message_info"],
                "match_score": state["latest_match_score"],
                "registered_faces": len(registry.known_faces()),
                "last_event_direction": state["last_event_direction"],
                "last_event_at": state["last_event_at"],
                "active_tracks": len(active_presence_tracks),
                "recent_people": list(state["recent_people"].values())[-10:],
            }
        )


@app.route("/api/presence_events")
def api_presence_events():
    return jsonify(
        {
            "active_tracks": len(active_presence_tracks),
            "events": db.get_presence_events(limit=20),
        }
    )


@app.route("/api/faces")
def api_faces():
    return jsonify(db.list_faces())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=CONFIG["port"], debug=False, threaded=True)
