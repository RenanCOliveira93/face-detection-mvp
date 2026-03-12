"""
Script de Treinamento
Lê as imagens de training_images/<face_id>/ e gera o modelo de encodings.

Estrutura esperada de pastas:
  training_images/
  ├── joao_silva/
  │   ├── foto1.jpg
  │   ├── foto2.jpg
  │   └── foto3.jpg
  └── maria_souza/
      ├── foto1.jpg
      └── foto2.jpg

O nome da subpasta = face_id cadastrado no banco de dados.
"""

import os
import sys
import pickle
import face_recognition
import cv2
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CONFIG
from database import FaceDatabase


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def train_model():
    training_dir = Path(CONFIG["training_images_dir"])
    model_path   = Path(CONFIG["model_path"])
    model_path.parent.mkdir(parents=True, exist_ok=True)

    if not training_dir.exists():
        print(f"[TRAIN] ❌ Pasta de treinamento não encontrada: {training_dir}")
        return

    db = FaceDatabase()
    known_encodings = []
    known_ids = []

    persons = [p for p in training_dir.iterdir() if p.is_dir()]
    if not persons:
        print("[TRAIN] ⚠️  Nenhuma subpasta encontrada em training_images/")
        return

    print(f"\n[TRAIN] Encontradas {len(persons)} pessoa(s) para treinar...\n")

    for person_dir in sorted(persons):
        face_id = person_dir.name
        profile = db.get_face(face_id)

        if not profile:
            print(f"  ⚠️  '{face_id}' não está cadastrado no banco. "
                  f"Execute: python scripts/register_face.py")
            continue

        images = [
            f for f in person_dir.iterdir()
            if f.suffix.lower() in SUPPORTED_EXTENSIONS
        ]

        if not images:
            print(f"  ⚠️  Nenhuma imagem encontrada em '{person_dir}'")
            continue

        print(f"  👤 {profile['full_name']} ({face_id}) — {len(images)} imagem(ns)")
        success_count = 0

        for img_path in images:
            img = face_recognition.load_image_file(str(img_path))

            # Detectar localização do rosto
            locations = face_recognition.face_locations(img, model="hog")

            if not locations:
                print(f"     ⚠️  Nenhum rosto detectado em {img_path.name}")
                continue

            if len(locations) > 1:
                print(f"     ⚠️  Múltiplos rostos em {img_path.name}, usando o primeiro")

            encodings = face_recognition.face_encodings(img, locations)
            if encodings:
                known_encodings.append(encodings[0])
                known_ids.append(face_id)
                success_count += 1

        print(f"     ✅ {success_count}/{len(images)} imagens processadas")

    if not known_encodings:
        print("\n[TRAIN] ❌ Nenhum encoding gerado. Verifique as imagens.")
        return

    # Salvar modelo
    with open(model_path, "wb") as f:
        pickle.dump({"encodings": known_encodings, "ids": known_ids}, f)

    print(f"\n[TRAIN] ✅ Modelo salvo em '{model_path}'")
    print(f"[TRAIN] Total de encodings: {len(known_encodings)}")
    print(f"[TRAIN] Pessoas únicas: {len(set(known_ids))}")
    print("\n[TRAIN] Reinicie o servidor para carregar o novo modelo.\n")


if __name__ == "__main__":
    train_model()
