"""Cria uma escola POC com dados fictícios e fotos reais já existentes localmente.

As imagens nunca são copiadas para o Git, renomeadas ou incluídas no relatório.
Somente o caminho local e o embedding são persistidos no SQLite informado.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import FaceDatabase
from face_registry import FaceRegistry, SUPPORTED_EXTENSIONS


def discover_face_images(images_dir: Path) -> list[Path]:
    if not images_dir.is_dir():
        raise FileNotFoundError(
            f"Diretório de fotos não encontrado: {images_dir}. "
            "Crie-o localmente; storage/ é intencionalmente ignorado pelo Git."
        )
    images = sorted(
        path for path in images_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    if not images:
        raise ValueError(f"Nenhuma imagem compatível encontrada em {images_dir}")
    return images


def seed_mock_school(db: FaceDatabase, images: list[Path], registry: FaceRegistry) -> dict:
    school_key = secrets.token_urlsafe(32)
    admin_key = secrets.token_urlsafe(32)
    professor_key = secrets.token_urlsafe(32)
    device_secret = secrets.token_urlsafe(32)

    school = db.create_school(
        "Escola POC Mock", "escola-poc-mock", school_key, "America/Sao_Paulo"
    )
    admin = db.add_school_member(
        school["id"], "Administrador POC", "admin@example.test", "school_admin", admin_key
    )
    professor = db.add_school_member(
        school["id"], "Professora POC", "professora@example.test", "professor", professor_key
    )
    classrooms = [
        db.create_classroom(school["id"], "Turma Mock A", "2026"),
        db.create_classroom(school["id"], "Turma Mock B", "2026"),
    ]
    students = []
    for index, image in enumerate(images, start=1):
        face_id = f"aluno_mock_{index:02d}"
        student = registry.register_face(
            full_name=f"Aluno Mock {index:02d}",
            phone=f"+551190000{index:04d}",
            image_path=str(image),
            face_id=face_id,
            email=f"responsavel{index:02d}@example.test",
            notes="Cadastro fictício para teste local autorizado.",
            school_id=school["id"],
            store_image=False,
        )
        classroom_id = classrooms[(index - 1) % len(classrooms)]
        db.enroll_student(school["id"], classroom_id, face_id)
        if index == 1:
            db.add_dietary_restriction(
                school["id"], face_id, "Restrição alimentar fictícia", "atenção"
            )
        students.append({"id": student["id"], "classroom_id": classroom_id})

    db.add_teacher_note(
        school["id"], professor["id"], "Anotação pedagógica fictícia.",
        classroom_id=classrooms[0],
    )
    db.add_kitchen_recipient(school["id"], "Cozinha Mock", "+5511900000000")
    db.create_device(school["id"], "portaria-mock", "Portaria Mock", device_secret)
    return {
        "school_id": school["id"],
        "school_key": school_key,
        "admin_key": admin_key,
        "professor_key": professor_key,
        "device_id": "portaria-mock",
        "device_secret": device_secret,
        "classroom_ids": classrooms,
        "students": students,
        "dashboard": db.get_school_dashboard(school["id"]),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images-dir", type=Path, default=Path("storage/faces"))
    parser.add_argument("--database", type=Path, default=Path("database/mock_school.db"))
    parser.add_argument("--output", type=Path, default=Path("database/mock_school_manifest.json"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.database.exists():
        raise SystemExit(
            f"Banco já existe: {args.database}. Remova-o explicitamente para recriar o seed."
        )
    images = discover_face_images(args.images_dir)
    args.database.parent.mkdir(parents=True, exist_ok=True)
    db = FaceDatabase(str(args.database), attendance_timezone="America/Sao_Paulo")
    manifest = seed_mock_school(db, images, FaceRegistry(db))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"SEED_OK: {len(manifest['students'])} alunos em {args.database}")
    print(f"Credenciais locais: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
