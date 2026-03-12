"""
Script de Demo: Popula o banco com dados de teste
e captura fotos pela webcam para o usuário de demonstração.

Uso:
  python scripts/demo_setup.py
"""

import sys
import os
import cv2
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import FaceDatabase
from config import CONFIG
from pathlib import Path


DEMO_FACES = [
    {
        "id": "demo_user",
        "full_name": "Usuário Demo",
        "phone": "5514997283283",   # destinatário configurado
        "email": "demo@empresa.com",
        "notes": "Cadastrado automaticamente pelo script de demo"
    }
]


def seed_database():
    db = FaceDatabase()
    print("\n[DEMO] Populando banco de dados com perfis de teste...")

    for face in DEMO_FACES:
        path = Path(CONFIG["training_images_dir"]) / face["id"]
        path.mkdir(parents=True, exist_ok=True)

        ok = db.add_face(
            face["id"], face["full_name"], face["phone"],
            face["email"], face["notes"]
        )
        status = "✅ Criado" if ok else "⚠️  Já existia"
        print(f"  {status}: {face['full_name']} (ID: {face['id']})")


def capture_training_photos(face_id: str = "demo_user", n_photos: int = 10):
    """Captura fotos pela webcam para treinar o rosto."""
    save_dir = Path(CONFIG["training_images_dir"]) / face_id
    save_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[DEMO] Capturando {n_photos} fotos para '{face_id}'...")
    print("  Posicione seu rosto em frente à câmera.")
    print("  Pressione ESPAÇO para capturar | ESC para sair\n")

    cap = cv2.VideoCapture(0)
    count = 0

    while count < n_photos:
        ret, frame = cap.read()
        if not ret:
            break

        display = frame.copy()
        cv2.putText(display, f"Foto {count}/{n_photos} — ESPAÇO para capturar",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 0), 2)
        cv2.imshow("Captura de Treinamento", display)

        key = cv2.waitKey(1) & 0xFF
        if key == 27:  # ESC
            break
        elif key == 32:  # ESPAÇO
            filename = save_dir / f"photo_{count+1:03d}.jpg"
            cv2.imwrite(str(filename), frame)
            print(f"  📸 Foto {count+1} salva: {filename.name}")
            count += 1
            time.sleep(0.3)

    cap.release()
    cv2.destroyAllWindows()

    print(f"\n[DEMO] {count} foto(s) capturada(s) em '{save_dir}'")
    if count > 0:
        print("[DEMO] Execute agora: python scripts/train_model.py")


if __name__ == "__main__":
    seed_database()

    choice = input("\nDeseja capturar fotos pela webcam agora? (S/n): ").strip().lower()
    if choice != "n":
        capture_training_photos()

    print("\n[DEMO] Setup concluído! Próximos passos:")
    print("  1. python scripts/train_model.py   (treinar modelo)")
    print("  2. python main.py                  (iniciar aplicação)")
    print(f"  3. Acesse http://localhost:{CONFIG['port']}\n")
