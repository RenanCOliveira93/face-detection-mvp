"""Cadastra ou atualiza um aluno usando apenas uma foto de referência."""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from face_registry import FaceRegistry, slugify


def register_interactive() -> None:
    registry = FaceRegistry()
    print("\n" + "=" * 60)
    print(" Cadastro de aluno com foto única ")
    print("=" * 60)
    full_name = input("Nome completo: ").strip()
    if not full_name:
        raise SystemExit("Nome é obrigatório.")
    suggested_id = slugify(full_name)
    face_id = input(f"ID [{suggested_id}]: ").strip() or suggested_id
    phone = input("Telefone do responsável (com DDI/DDD): ").strip()
    image_path = input("Caminho da foto do aluno: ").strip()
    student = registry.register_face(full_name=full_name, phone=phone, image_path=image_path, face_id=face_id)
    print(f"\n✅ Aluno salvo: {student['full_name']} ({student['id']})")
    print(f"📷 Foto salva em: {student['photo_path']}")


def register_from_args(args: argparse.Namespace) -> None:
    registry = FaceRegistry()
    student = registry.register_face(
        full_name=args.name,
        phone=args.phone,
        image_path=args.image,
        face_id=args.id,
        email=args.email,
        notes=args.notes,
    )
    print(f"✅ Aluno salvo: {student['full_name']} ({student['id']})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cadastra aluno com uma única foto")
    parser.add_argument("--id", help="ID único do aluno")
    parser.add_argument("--name", help="Nome completo")
    parser.add_argument("--phone", help="Telefone do responsável")
    parser.add_argument("--image", help="Caminho da foto do aluno")
    parser.add_argument("--email", default="", help="E-mail")
    parser.add_argument("--notes", default="", help="Observações")
    args = parser.parse_args()

    if args.name and args.phone and args.image:
        register_from_args(args)
    else:
        register_interactive()
