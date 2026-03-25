"""Aplicação Flask com reconhecimento facial por similaridade usando uma foto por aluno."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import cv2
import face_recognition
from flask import Flask, Response, jsonify, render_template

from config import CONFIG
from database import FaceDatabase
from face_registry import FaceRegistry
from messaging import send_whatsapp_message

app = Flask(__name__)
db = FaceDatabase()
registry = FaceRegistry(db)

PRESENCE_EXIT_TIMEOUT_SECONDS = 8
MAX_PRESENCE_EVENTS = 200

state = {
    "status": "idle",
    "person": None,
    "message_sent": False,
    "message_info": None,
    "match_score": None,
    "last_detection": None,
    "frame_info": [],
    "last_event_direction": None,
    "last_event_at": None,
}
state_lock = threading.Lock()
last_message_time: dict[str, float] = {}
active_presence_tracks: dict[str, dict] = {}
presence_events: list[dict] = []


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_presence_event(person: dict, direction: str, match_score: float | None) -> None:
    event = {
        "face_id": person["id"],
        "full_name": person["full_name"],
        "phone": person["phone"],
        "direction": direction,
        "event_at": _iso_now(),
        "match_score": match_score,
    }
    with state_lock:
        presence_events.insert(0, event)
        del presence_events[MAX_PRESENCE_EVENTS:]
        state["last_event_direction"] = direction
        state["last_event_at"] = event["event_at"]


def _expire_presence_tracks(now: float) -> None:
    to_close = []
    with state_lock:
        for face_id, track in active_presence_tracks.items():
            if now - track["last_seen"] > PRESENCE_EXIT_TIMEOUT_SECONDS:
                to_close.append(track["person"])
        for person in to_close:
            active_presence_tracks.pop(person["id"], None)

    for person in to_close:
        _record_presence_event(person, "saida", None)


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
                                "person": None,
                                "message_sent": False,
                                "message_info": None,
                                "match_score": match_score,
                            }
                        )
            with state_lock:
                state["frame_info"] = current_faces
                if not current_faces:
                    state.update(
                        {
                            "status": "idle",
                            "person": None,
                            "message_sent": False,
                            "message_info": None,
                            "match_score": None,
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
    cooldown = CONFIG["message_cooldown"]
    with state_lock:
        state.update(
            {
                "status": "recognized",
                "person": person,
                "match_score": match_score,
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
        _record_presence_event(person, "entrada", match_score)

    if now - last_message_time.get(person["id"], 0) < cooldown:
        return
    last_message_time[person["id"]] = now
    threading.Thread(target=_send_message_async, args=(person, match_score), daemon=True).start()


def _send_message_async(person: dict, match_score: float | None):
    message = f"Aluno {person['full_name']} chegou na escola."
    success, info = send_whatsapp_message(person["phone"], message)
    db.log_detection(person["id"], similarity=match_score, message_ok=success, message_info=str(info))
    with state_lock:
        state["message_sent"] = success
        state["message_info"] = info


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
                "person": state["person"],
                "message_sent": state["message_sent"],
                "message_info": state["message_info"],
                "match_score": state["match_score"],
                "registered_faces": len(registry.known_faces()),
                "last_event_direction": state["last_event_direction"],
                "last_event_at": state["last_event_at"],
                "active_tracks": len(active_presence_tracks),
            }
        )


@app.route("/api/presence_events")
def api_presence_events():
    with state_lock:
        return jsonify(
            {
                "active_tracks": len(active_presence_tracks),
                "events": presence_events[:20],
            }
        )


@app.route("/api/faces")
def api_faces():
    return jsonify(db.list_faces())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=CONFIG["port"], debug=False, threaded=True)
