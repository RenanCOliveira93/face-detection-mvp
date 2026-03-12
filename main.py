"""
Face Recognition MVP - Aplicação Principal
Fluxo: Webcam → Detecção de Rosto → Reconhecimento → Alerta/WhatsApp
"""

import cv2
import time
import threading
import pickle
import os
import numpy as np
import face_recognition
from flask import Flask, render_template, Response, jsonify, request
from database import FaceDatabase
from messaging import send_whatsapp_message
from config import CONFIG

app = Flask(__name__)

# ─────────────────────────────────────────────
# Estado global da aplicação
# ─────────────────────────────────────────────
state = {
    "status": "idle",          # idle | recognized | unknown | alert
    "person": None,            # dict com dados da pessoa
    "message_sent": False,
    "last_detection": 0,
    "alert_active": False,
    "frame_info": []           # lista de rostos detectados no frame atual
}
state_lock = threading.Lock()

# Cooldown: quantos segundos esperar antes de reenviar mensagem para a mesma pessoa
MESSAGE_COOLDOWN = 30
last_message_time = {}

# ─────────────────────────────────────────────
# Carregar encodings treinados
# ─────────────────────────────────────────────
known_encodings = []
known_ids = []

def load_model():
    global known_encodings, known_ids
    model_path = CONFIG["model_path"]
    if os.path.exists(model_path):
        with open(model_path, "rb") as f:
            data = pickle.load(f)
            known_encodings = data["encodings"]
            known_ids = data["ids"]
        print(f"[MODEL] ✅ Modelo carregado: {len(known_encodings)} rosto(s) treinado(s)")
    else:
        print("[MODEL] ⚠️  Nenhum modelo encontrado. Execute: python scripts/train_model.py")

load_model()
db = FaceDatabase()

# ─────────────────────────────────────────────
# Gerador de frames da webcam
# ─────────────────────────────────────────────
def generate_frames():
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    frame_count = 0
    process_every = 3  # processar reconhecimento a cada N frames (performance)

    while True:
        success, frame = cap.read()
        if not success:
            break

        frame_count += 1
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # Reduzir resolução para processamento mais rápido
        small_frame = cv2.resize(rgb_frame, (0, 0), fx=0.5, fy=0.5)

        current_faces = []

        if frame_count % process_every == 0 and len(known_encodings) > 0:
            face_locations = face_recognition.face_locations(small_frame, model="hog")
            face_encodings = face_recognition.face_encodings(small_frame, face_locations)

            for enc, loc in zip(face_encodings, face_locations):
                matches = face_recognition.compare_faces(
                    known_encodings, enc,
                    tolerance=CONFIG["recognition_tolerance"]
                )
                distances = face_recognition.face_distance(known_encodings, enc)

                person_id = None
                person_data = None
                is_known = False

                if True in matches:
                    best_idx = np.argmin(distances)
                    if matches[best_idx]:
                        person_id = known_ids[best_idx]
                        person_data = db.get_face(person_id)
                        is_known = True

                # Escalar coordenadas de volta ao tamanho original
                top, right, bottom, left = [v * 2 for v in loc]

                current_faces.append({
                    "location": (top, right, bottom, left),
                    "known": is_known,
                    "person": person_data
                })

                # Disparar mensagem e atualizar estado
                if is_known and person_data:
                    _handle_recognized(person_data)
                else:
                    with state_lock:
                        state["status"] = "unknown"
                        state["alert_active"] = True
                        state["person"] = None

            if not face_locations:
                with state_lock:
                    state["status"] = "idle"
                    state["alert_active"] = False

            with state_lock:
                state["frame_info"] = current_faces

        # ── Desenhar bounding boxes ──
        with state_lock:
            faces_to_draw = state["frame_info"]

        for face in faces_to_draw:
            top, right, bottom, left = face["location"]
            if face["known"] and face["person"]:
                color = (0, 200, 0)   # Verde
                label = face["person"]["full_name"]
            else:
                color = (0, 0, 220)   # Vermelho (BGR)
                label = "DESCONHECIDO"

            cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
            cv2.rectangle(frame, (left, bottom - 28), (right, bottom), color, cv2.FILLED)
            cv2.putText(frame, label, (left + 6, bottom - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

        # Encode para MJPEG
        ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

    cap.release()


def _handle_recognized(person_data):
    global last_message_time
    pid = person_data["id"]
    now = time.time()

    with state_lock:
        state["status"] = "recognized"
        state["alert_active"] = False
        state["person"] = person_data

    # Verificar cooldown antes de enviar
    if now - last_message_time.get(pid, 0) > MESSAGE_COOLDOWN:
        last_message_time[pid] = now
        threading.Thread(
            target=_send_message_async,
            args=(person_data,),
            daemon=True
        ).start()


def _send_message_async(person_data):
    name = person_data["full_name"]
    phone = person_data["phone"]
    message = f"{name} acabou de chegar aqui. 🟢"

    success, info = send_whatsapp_message(phone, message)

    with state_lock:
        state["message_sent"] = success
        state["last_detection"] = time.time()

    status = "✅ Mensagem enviada" if success else f"❌ Erro: {info}"
    print(f"[MSG] {status} → {phone}")


# ─────────────────────────────────────────────
# Rotas Flask
# ─────────────────────────────────────────────
@app.route("/")
def index():
    faces = db.list_faces()
    model_loaded = len(known_encodings) > 0
    return render_template("index.html", faces=faces, model_loaded=model_loaded,
                           config=CONFIG)


@app.route("/video_feed")
def video_feed():
    return Response(generate_frames(),
                    mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/api/status")
def api_status():
    with state_lock:
        return jsonify({
            "status": state["status"],
            "alert_active": state["alert_active"],
            "person": state["person"],
            "message_sent": state["message_sent"],
            "model_loaded": len(known_encodings) > 0,
            "trained_faces": len(known_ids)
        })


@app.route("/api/reload_model", methods=["POST"])
def reload_model():
    load_model()
    return jsonify({"ok": True, "count": len(known_encodings)})


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("  🎥  Face Recognition MVP")
    print("=" * 50)
    print(f"  Acesse: http://localhost:{CONFIG['port']}")
    print("=" * 50 + "\n")
    app.run(host="0.0.0.0", port=CONFIG["port"], debug=False, threaded=True)
