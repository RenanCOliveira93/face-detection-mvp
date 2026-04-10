"""Aplicação Flask com reconhecimento facial por similaridade usando uma foto por aluno."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

import os
import tempfile

import cv2
import face_recognition
from flask import Flask, Response, jsonify, render_template, request

from config import CONFIG
from database import FaceDatabase
from face_registry import FaceRegistry, slugify
from integrations.webhook_client import publish_presence_event
from messaging import send_whatsapp_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
db = FaceDatabase(attendance_timezone=CONFIG["attendance_timezone"])
registry = FaceRegistry(db)

_CORS_ORIGIN = os.getenv("CORS_ORIGIN", "*")


@app.after_request
def _add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = _CORS_ORIGIN
    response.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,DELETE,OPTIONS"
    return response

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


@app.route("/api/daily_attendance")
def api_daily_attendance():
    return jsonify(
        {
            "timezone": CONFIG["attendance_timezone"],
            "items": db.get_daily_attendance(limit=200),
        }
    )


@app.route("/api/faces")
def api_faces():
    faces = db.list_faces()
    result = []
    for f in faces:
        result.append({
            "id": f["id"],
            "face_id": f["id"],
            "full_name": f["full_name"],
            "phone": f.get("phone"),
            "email": f.get("email"),
            "notes": f.get("notes"),
            "photo_path": f.get("photo_path"),
            "has_encoding": f.get("has_encoding", False),
            "created_at": f.get("created_at"),
            "notification_phone": f.get("notification_phone"),
        })
    return jsonify(result)


@app.route("/api/register", methods=["POST", "OPTIONS"])
def api_register():
    if request.method == "OPTIONS":
        return "", 204

    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    email = request.form.get("email", "").strip()
    notes = request.form.get("notes", "").strip()
    custom_id = request.form.get("id", "").strip() or None
    image_file = request.files.get("image")

    if not name or not phone:
        return jsonify({"success": False, "error": "Nome e telefone são obrigatórios"}), 400

    tmp_path = None
    try:
        if image_file and image_file.filename:
            ext = os.path.splitext(image_file.filename)[1].lower() or ".jpg"
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=ext)
            os.close(tmp_fd)
            image_file.save(tmp_path)
            student = registry.register_face(
                full_name=name,
                phone=phone,
                image_path=tmp_path,
                face_id=custom_id,
                email=email,
                notes=notes,
            )
        else:
            # Cadastro sem foto — aluno não será reconhecido até foto ser adicionada
            person_id = custom_id or slugify(name)
            existing = db.get_face(person_id)
            payload = dict(full_name=name, phone=phone, email=email, notes=notes)
            if existing:
                db.update_face(person_id, **payload)
            else:
                db.add_face(face_id=person_id, **payload)
            student = db.get_face(person_id)

        # Recarrega known_faces para reconhecimento imediato
        registry.known_faces()

        return jsonify({"success": True, "student": student})
    except (FileNotFoundError, ValueError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        logger.exception("api_register_error", extra={"name": name, "error": str(exc)})
        return jsonify({"success": False, "error": "Erro interno ao cadastrar aluno"}), 500
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=CONFIG["port"], debug=False, threaded=True)
