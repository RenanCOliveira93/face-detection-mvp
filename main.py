"""Aplicação Flask com reconhecimento facial por similaridade usando uma foto por aluno."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

import os
import tempfile
from functools import wraps

import cv2
import face_recognition
from flask import Flask, Response, jsonify, render_template, request, session

from config import CONFIG
from database import FaceDatabase
from face_registry import FaceRegistry, slugify
from integrations.webhook_client import publish_presence_event
from messaging import send_whatsapp_message
from recognition_pipeline import recognize_batch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = CONFIG["session_secret"]
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=CONFIG["session_cookie_secure"],
    SESSION_COOKIE_SAMESITE=CONFIG["session_cookie_samesite"],
)
db = FaceDatabase(attendance_timezone=CONFIG["attendance_timezone"])
registry = FaceRegistry(db)

# Suporta tanto a variável legada CORS_ORIGIN (uma origem) quanto CORS_ORIGINS
# (lista separada por vírgula, via config.py). Com sessão/cookie de login, o
# navegador exige uma origem explícita — "*" nunca funciona com credentials.
_CORS_ALLOWED_ORIGINS = set(CONFIG["cors_origins"])
_legacy_origin = os.getenv("CORS_ORIGIN", "").strip()
if _legacy_origin and _legacy_origin != "*":
    _CORS_ALLOWED_ORIGINS.add(_legacy_origin)
if not _CORS_ALLOWED_ORIGINS or os.getenv("CORS_ORIGIN", "").strip() == "*":
    logger.warning(
        "CORS_ORIGIN=* ou nenhuma origem configurada: login por sessão/cookie não "
        "funcionará entre origens diferentes. Configure CORS_ORIGINS com a origem "
        "real da interface (ex.: http://localhost:8080)."
    )


def _principal():
    # 1) Sessão de login real (cookie httpOnly) — fluxo da interface web humana.
    stored = session.get("principal")
    if stored:
        return stored
    # 2) X-School-Key/dispositivo/integração — fluxo programático (curl, Control iD,
    #    kiosk sem usuário logado). /video_feed é consumido por uma tag <img>, que não
    #    envia headers customizados, então aceitamos a chave também via query string
    #    apenas para esse fluxo de imagem.
    api_key = request.headers.get("X-School-Key", "").strip() or request.args.get("key", "").strip()
    if not api_key:
        return None
    member = db.authenticate_principal(api_key)
    if member:
        return member
    school = db.get_school_by_api_key(api_key)
    if school:
        return {"school_id": school["id"], "role": "integration", "timezone": school["timezone"]}
    return None


def require_roles(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            principal = _principal()
            if not principal:
                return jsonify({"error": "Credencial ausente ou inválida"}), 401
            if principal["role"] not in roles:
                return jsonify({"error": "Papel sem permissão para este recurso"}), 403
            return view(*args, principal=principal, **kwargs)
        return wrapped
    return decorator


@app.after_request
def _add_cors(response):
    origin = request.headers.get("Origin", "")
    if origin in _CORS_ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
    elif not _CORS_ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization,X-School-Key"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,DELETE,OPTIONS"
    return response


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    payload = request.get_json(silent=True) or {}
    api_key = payload.get("api_key", "").strip()
    if not api_key:
        return jsonify({"error": "api_key é obrigatório"}), 400
    principal = db.authenticate_principal(api_key)
    if not principal:
        return jsonify({"error": "Credencial inválida"}), 401
    session["principal"] = principal
    session.permanent = True
    return jsonify(principal)


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    session.clear()
    return "", 204


@app.route("/api/auth/me")
def api_auth_me():
    principal = session.get("principal")
    if not principal:
        return jsonify({"error": "Não autenticado"}), 401
    return jsonify(principal)

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

            matches = recognize_batch(encodings, registry.match_encoding)
            for match, location in zip(matches, locations):
                person, match_score = match["person"], match["match_score"]
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
    return render_template("index.html", faces=[], config=CONFIG)


@app.route("/video_feed")
@require_roles("school_admin", "professor", "integration")
def video_feed(principal):
    return Response(generate_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/api/status")
@require_roles("school_admin", "professor", "integration")
def api_status(principal):
    with state_lock:
        return jsonify(
            {
                "status": state["status"],
                "person": state["latest_person"] if (state["latest_person"] or {}).get("school_id") == principal["school_id"] else None,
                "message_sent": state["last_message_sent"],
                "message_info": state["last_message_info"],
                "match_score": state["latest_match_score"],
                "registered_faces": len(db.list_faces(school_id=principal["school_id"])),
                "last_event_direction": state["last_event_direction"],
                "last_event_at": state["last_event_at"],
                "active_tracks": sum(
                    1 for track in active_presence_tracks.values()
                    if track["person"].get("school_id") == principal["school_id"]
                ),
                "recent_people": [
                    item for face_id, item in state["recent_people"].items()
                    if (db.get_face(face_id) or {}).get("school_id") == principal["school_id"]
                ][-10:],
            }
        )


@app.route("/api/presence_events")
@require_roles("school_admin", "professor", "integration")
def api_presence_events(principal):
    return jsonify(
        {
            "active_tracks": sum(
                1 for track in active_presence_tracks.values()
                if track["person"].get("school_id") == principal["school_id"]
            ),
            "events": db.get_presence_events(limit=20, school_id=principal["school_id"]),
        }
    )


@app.route("/api/daily_attendance")
@require_roles("school_admin", "professor", "integration")
def api_daily_attendance(principal):
    return jsonify(
        {
            "timezone": CONFIG["attendance_timezone"],
            "items": db.get_daily_attendance(limit=200, school_id=principal["school_id"]),
        }
    )


@app.route("/api/attendance-book")
@require_roles("school_admin", "professor", "integration")
def api_attendance_book(principal):
    attendance_date = request.args.get("date", "").strip() or None
    try:
        return jsonify(db.get_attendance_book(principal["school_id"], attendance_date))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/faces")
@require_roles("school_admin", "professor", "integration")
def api_faces(principal):
    faces = db.list_faces(school_id=principal["school_id"])
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
@require_roles("school_admin", "integration")
def api_register(principal):
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
                school_id=principal["school_id"],
            )
        else:
            # Cadastro sem foto — aluno não será reconhecido até foto ser adicionada
            person_id = custom_id or slugify(name)
            existing = db.get_face(person_id)
            if (
                existing
                and existing.get("school_id") not in (None, principal["school_id"])
            ):
                return jsonify({"success": False, "error": "Aluno já pertence a outra escola"}), 409
            payload = dict(
                full_name=name, phone=phone, email=email, notes=notes,
                school_id=principal["school_id"],
            )
            if existing:
                db.update_face(person_id, **payload)
            else:
                db.add_face(face_id=person_id, **payload)
            student = db.get_face(person_id)

        # Recarrega known_faces para reconhecimento imediato
        if student:
            if not db.assign_face_to_school(student["id"], principal["school_id"]):
                return jsonify({"success": False, "error": "Aluno já pertence a outra escola"}), 409
            student = db.get_face(student["id"])
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


@app.route("/api/schools", methods=["POST"])
def api_create_school():
    token = CONFIG.get("admin_bootstrap_token")
    if not token or request.headers.get("X-Bootstrap-Token") != token:
        return jsonify({"error": "Bootstrap não autorizado"}), 401
    payload = request.get_json(silent=True) or {}
    if not payload.get("name") or not payload.get("slug"):
        return jsonify({"error": "name e slug são obrigatórios"}), 400
    try:
        school = db.create_school(
            payload["name"], payload["slug"], timezone_name=payload.get("timezone", "UTC")
        )
        admin = None
        if payload.get("admin_name") and payload.get("admin_email"):
            admin = db.add_school_member(
                school["id"], payload["admin_name"], payload["admin_email"], "school_admin"
            )
        return jsonify({"school": school, "admin": admin}), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 409


@app.route("/api/school/dashboard")
@require_roles("school_admin", "professor", "integration")
def api_school_dashboard(principal):
    return jsonify(db.get_school_dashboard(principal["school_id"]))


@app.route("/api/school/members", methods=["GET", "POST"])
@require_roles("school_admin")
def api_school_members(principal):
    if request.method == "GET":
        return jsonify({"items": db.list_school_members(principal["school_id"])})
    payload = request.get_json(silent=True) or {}
    try:
        member = db.add_school_member(
            principal["school_id"],
            payload.get("full_name", ""),
            payload.get("email", ""),
            payload.get("role", "professor"),
        )
        return jsonify(member), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/school/classrooms", methods=["GET", "POST"])
@app.route("/api/classes", methods=["GET", "POST"])
@require_roles("school_admin", "professor")
def api_school_classrooms(principal):
    if request.method == "GET":
        return jsonify({"items": db.list_classrooms(principal["school_id"])})
    if principal["role"] != "school_admin":
        return jsonify({"error": "Papel sem permissão para este recurso"}), 403
    payload = request.get_json(silent=True) or {}
    if not payload.get("name"):
        return jsonify({"error": "name é obrigatório"}), 400
    try:
        classroom_id = db.create_classroom(
            principal["school_id"], payload["name"], payload.get("school_year", "")
        )
        return jsonify({"id": classroom_id}), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 409


@app.route("/api/school/classrooms/<int:classroom_id>", methods=["PUT", "DELETE"])
@require_roles("school_admin")
def api_update_classroom(classroom_id: int, principal):
    if request.method == "DELETE":
        try:
            db.deactivate_classroom(principal["school_id"], classroom_id)
            return "", 204
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404
    payload = request.get_json(silent=True) or {}
    try:
        db.update_classroom(
            principal["school_id"], classroom_id, payload.get("name", ""), payload.get("school_year", "")
        )
        return jsonify({"success": True})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/school/students/<face_id>/assign", methods=["POST"])
@require_roles("school_admin")
def api_assign_student(face_id: str, principal):
    if not db.assign_face_to_school(face_id, principal["school_id"]):
        return jsonify({"error": "Aluno não encontrado"}), 404
    return jsonify({"success": True})


@app.route("/api/school/students/<face_id>", methods=["DELETE"])
@require_roles("school_admin")
def api_erase_student(face_id: str, principal):
    try:
        photo_path = db.erase_student_personal_data(principal["school_id"], face_id)
        if photo_path and os.path.isfile(photo_path):
            os.unlink(photo_path)
        registry.known_faces()
        return "", 204
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404


@app.route("/api/school/classrooms/<int:classroom_id>/students", methods=["GET", "POST"])
@require_roles("school_admin", "professor")
def api_enroll_student(classroom_id: int, principal):
    if request.method == "GET":
        try:
            return jsonify({"items": db.list_classroom_students(principal["school_id"], classroom_id)})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404
    if principal["role"] != "school_admin":
        return jsonify({"error": "Papel sem permissão para este recurso"}), 403
    payload = request.get_json(silent=True) or {}
    try:
        db.enroll_student(principal["school_id"], classroom_id, payload.get("face_id", ""))
        return jsonify({"success": True}), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/school/notes", methods=["POST"])
@require_roles("school_admin", "professor")
def api_teacher_notes(principal):
    payload = request.get_json(silent=True) or {}
    try:
        note_id = db.add_teacher_note(
            principal["school_id"], principal.get("member_id", 0), payload.get("body", ""),
            face_id=payload.get("face_id"), classroom_id=payload.get("classroom_id"),
        )
        return jsonify({"id": note_id}), 201
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400


def _forbidden_unless_assigned(principal, school_id: int, classroom_id: int, subject_id: int):
    """Admin pode tudo; professor só atua em turma+disciplina onde está alocado."""
    if principal["role"] == "school_admin":
        return None
    member_id = principal.get("member_id")
    if not member_id or not db.is_teacher_assigned(school_id, member_id, classroom_id, subject_id):
        return jsonify({"error": "Professor não está alocado nesta turma/disciplina"}), 403
    return None


def _forbidden_unless_lesson_owner(principal, lesson: dict):
    if principal["role"] == "school_admin":
        return None
    if lesson["teacher_member_id"] != principal.get("member_id"):
        return jsonify({"error": "Aula pertence a outro professor"}), 403
    return None


@app.route("/api/subjects", methods=["GET", "POST"])
@require_roles("school_admin", "professor")
def api_subjects(principal):
    if request.method == "GET":
        return jsonify({"items": db.list_subjects(principal["school_id"])})
    if principal["role"] != "school_admin":
        return jsonify({"error": "Papel sem permissão para este recurso"}), 403
    payload = request.get_json(silent=True) or {}
    try:
        subject = db.create_subject(principal["school_id"], payload.get("name", ""))
        return jsonify(subject), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/subjects/<int:subject_id>", methods=["PUT", "DELETE"])
@require_roles("school_admin")
def api_update_subject(subject_id: int, principal):
    if request.method == "DELETE":
        try:
            db.deactivate_subject(principal["school_id"], subject_id)
            return "", 204
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404
    payload = request.get_json(silent=True) or {}
    try:
        db.update_subject(principal["school_id"], subject_id, payload.get("name", ""))
        return jsonify({"success": True})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/report-card", methods=["GET"])
@require_roles("school_admin", "professor")
def api_report_card(principal):
    classroom_id = request.args.get("classroom_id", type=int)
    face_id = request.args.get("face_id")
    if not classroom_id:
        return jsonify({"error": "classroom_id é obrigatório"}), 400
    try:
        return jsonify({"items": db.get_report_card(principal["school_id"], classroom_id, face_id)})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404


@app.route("/api/school/teacher-assignments", methods=["GET", "POST"])
@require_roles("school_admin", "professor")
def api_teacher_assignments(principal):
    if request.method == "GET":
        member_id = request.args.get("member_id", type=int) if principal["role"] == "school_admin" else principal.get("member_id")
        return jsonify({"items": db.list_teacher_assignments(principal["school_id"], member_id)})
    if principal["role"] != "school_admin":
        return jsonify({"error": "Papel sem permissão para este recurso"}), 403
    payload = request.get_json(silent=True) or {}
    try:
        assignment = db.create_teacher_assignment(
            principal["school_id"], payload.get("member_id"),
            payload.get("classroom_id"), payload.get("subject_id"),
        )
        return jsonify(assignment), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/lessons", methods=["POST"])
@require_roles("school_admin", "professor")
def api_open_lesson(principal):
    payload = request.get_json(silent=True) or {}
    classroom_id = payload.get("classroom_id")
    subject_id = payload.get("subject_id")
    lesson_date = payload.get("lesson_date", "").strip()
    teacher_member_id = principal.get("member_id") if principal["role"] == "professor" else payload.get("teacher_member_id")
    if not lesson_date:
        return jsonify({"error": "lesson_date é obrigatório (YYYY-MM-DD)"}), 400
    if not teacher_member_id:
        return jsonify({"error": "teacher_member_id é obrigatório"}), 400
    forbidden = _forbidden_unless_assigned(principal, principal["school_id"], classroom_id, subject_id)
    if forbidden:
        return forbidden
    try:
        lesson = db.open_lesson(
            principal["school_id"], classroom_id, subject_id, teacher_member_id, lesson_date
        )
        return jsonify(lesson), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/lessons/<int:lesson_id>", methods=["GET"])
@require_roles("school_admin", "professor")
def api_get_lesson(lesson_id: int, principal):
    lesson = db.get_lesson(principal["school_id"], lesson_id)
    if not lesson:
        return jsonify({"error": "Aula não encontrada"}), 404
    forbidden = _forbidden_unless_lesson_owner(principal, lesson)
    if forbidden:
        return forbidden
    return jsonify(lesson)


@app.route("/api/lessons/<int:lesson_id>/attendance/<face_id>", methods=["PUT"])
@require_roles("school_admin", "professor")
def api_update_lesson_attendance(lesson_id: int, face_id: str, principal):
    lesson = db.get_lesson(principal["school_id"], lesson_id)
    if not lesson:
        return jsonify({"error": "Aula não encontrada"}), 404
    forbidden = _forbidden_unless_lesson_owner(principal, lesson)
    if forbidden:
        return forbidden
    payload = request.get_json(silent=True) or {}
    try:
        db.update_lesson_attendance(
            principal["school_id"], lesson_id, face_id, payload.get("status", ""),
            principal.get("member_id"),
        )
        return jsonify({"success": True})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/lessons/<int:lesson_id>/confirm", methods=["POST"])
@require_roles("school_admin", "professor")
def api_confirm_lesson(lesson_id: int, principal):
    lesson = db.get_lesson(principal["school_id"], lesson_id)
    if not lesson:
        return jsonify({"error": "Aula não encontrada"}), 404
    forbidden = _forbidden_unless_lesson_owner(principal, lesson)
    if forbidden:
        return forbidden
    try:
        db.confirm_lesson(principal["school_id"], lesson_id, principal.get("member_id"))
        return jsonify({"success": True})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/lessons/<int:lesson_id>/content", methods=["PUT"])
@require_roles("school_admin", "professor")
def api_update_lesson_content(lesson_id: int, principal):
    lesson = db.get_lesson(principal["school_id"], lesson_id)
    if not lesson:
        return jsonify({"error": "Aula não encontrada"}), 404
    forbidden = _forbidden_unless_lesson_owner(principal, lesson)
    if forbidden:
        return forbidden
    payload = request.get_json(silent=True) or {}
    try:
        db.update_lesson_content(principal["school_id"], lesson_id, payload.get("content", ""))
        return jsonify({"success": True})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/assessments", methods=["GET", "POST"])
@require_roles("school_admin", "professor")
def api_assessments(principal):
    if request.method == "GET":
        classroom_id = request.args.get("classroom_id", type=int)
        subject_id = request.args.get("subject_id", type=int)
        return jsonify({"items": db.list_assessments(principal["school_id"], classroom_id, subject_id)})
    payload = request.get_json(silent=True) or {}
    classroom_id = payload.get("classroom_id")
    subject_id = payload.get("subject_id")
    forbidden = _forbidden_unless_assigned(principal, principal["school_id"], classroom_id, subject_id)
    if forbidden:
        return forbidden
    try:
        assessment = db.create_assessment(
            principal["school_id"], classroom_id, subject_id,
            payload.get("title", ""), payload.get("assessment_date", ""),
            payload.get("max_score", 10), principal.get("member_id"),
        )
        return jsonify(assessment), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/assessments/<int:assessment_id>/grades", methods=["GET", "POST"])
@require_roles("school_admin", "professor")
def api_assessment_grades(assessment_id: int, principal):
    if request.method == "GET":
        try:
            return jsonify({"items": db.list_grades(principal["school_id"], assessment_id)})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404
    payload = request.get_json(silent=True) or {}
    try:
        db.upsert_grade(
            principal["school_id"], assessment_id, payload.get("face_id", ""),
            float(payload.get("score")), principal.get("member_id"),
        )
        return jsonify({"success": True}), 201
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/dietary-restrictions", methods=["POST"])
@require_roles("school_admin")
def api_dietary_restrictions(principal):
    payload = request.get_json(silent=True) or {}
    try:
        item_id = db.add_dietary_restriction(
            principal["school_id"], payload.get("face_id", ""),
            payload.get("description", ""), payload.get("severity", "atenção"),
        )
        return jsonify({"id": item_id}), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/kitchen/recipients", methods=["POST"])
@require_roles("school_admin")
def api_kitchen_recipient(principal):
    payload = request.get_json(silent=True) or {}
    try:
        item_id = db.add_kitchen_recipient(
            principal["school_id"], payload.get("name", ""), payload.get("phone", "")
        )
        return jsonify({"id": item_id}), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/kitchen/dispatch", methods=["POST"])
@require_roles("school_admin")
def api_kitchen_dispatch(principal):
    payload = request.get_json(silent=True) or {}
    try:
        dispatch = db.prepare_kitchen_dispatch(principal["school_id"], payload.get("date"))
        if dispatch["duplicate"]:
            return jsonify({**dispatch, "message_results": []})
        summary = dispatch["payload"]
        message = "Cozinha escolar - " + summary["business_date"] + ": " + "; ".join(
            f"{item['classroom']}={item['present']} presentes" for item in summary["present_by_class"]
        )
        results = [send_whatsapp_message(item["phone"], message)[0] for item in dispatch["recipients"]]
        success = bool(results) and all(results)
        db.finish_kitchen_dispatch(principal["school_id"], summary["business_date"], success)
        return jsonify({**dispatch, "message_results": results})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/new_user_identified.fcgi", methods=["POST"])
def control_id_callback():
    device_id = request.headers.get("X-Device-Id", "")
    device_secret = request.headers.get("X-Device-Secret", "")
    device = db.authenticate_device(device_id, device_secret)
    if not device:
        return jsonify({"error": "Dispositivo não autorizado"}), 401
    payload = request.get_json(silent=True) or request.form.to_dict()
    external_id = str(payload.get("event_id", "")).strip()
    face_id = str(payload.get("user_id", "")).strip()
    if not external_id or not face_id:
        return jsonify({"error": "event_id e user_id são obrigatórios"}), 400
    try:
        event_id, direction, duplicate = db.record_device_presence(
            device["school_id"], device_id, external_id, face_id, payload.get("event_at")
        )
        if not duplicate:
            person = db.get_face(face_id)
            event = {
                "id": event_id,
                "face_id": face_id,
                "direction": direction,
                "event_at": payload.get("event_at") or _iso_now(),
                "match_score": None,
                "school_id": device["school_id"],
                "device_id": device_id,
            }
            _publish_presence_webhook(event, person or {"id": face_id, "school_id": device["school_id"]})
        return jsonify({"access": "granted", "event_id": event_id, "direction": direction, "duplicate": duplicate})
    except ValueError as exc:
        return jsonify({"access": "denied", "error": str(exc)}), 403


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=CONFIG["port"], debug=False, threaded=True)
