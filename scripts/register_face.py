"""Cadastra ou atualiza um aluno usando apenas uma foto de referência.

Suporta os campos estendidos do MVP: matrícula, turma, restrições alimentares.
Quando o Supabase está habilitado, o cadastro propaga turma + restrições para
a base mestre. Quando offline, apenas o cache local é gravado.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from face_registry import FaceRegistry, slugify


def _parse_restriction_ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [r.strip() for r in raw.split(",") if r.strip()]


def register_interactive() -> None:
    registry = FaceRegistry()
    print("\n" + "=" * 60)
    print(" Cadastro de aluno com foto única ")
    print("=" * 60)
    full_name = input("Nome completo: ").strip()
    if not full_name:
        raise SystemExit("Nome é obrigatório.")
    suggested_id = slugify(full_name)
    face_id = input(f"ID/slug [{suggested_id}]: ").strip() or suggested_id
    phone = input("Telefone do responsável (com DDI/DDD): ").strip()
    enrollment = input("Matrícula (opcional): ").strip() or None
    class_id = input("UUID da turma (opcional, copia do painel): ").strip() or None
    restrictions_raw = input(
        "UUIDs de restrições alimentares (separados por vírgula, opcional): "
    ).strip()
    image_path = input("Caminho da foto do aluno: ").strip()
    student = registry.register_face(
        full_name=full_name,
        phone=phone,
        image_path=image_path,
        face_id=face_id,
        enrollment_number=enrollment,
        class_id=class_id,
        restriction_ids=_parse_restriction_ids(restrictions_raw),
    )
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
        enrollment_number=args.enrollment,
        class_id=args.class_id,
        restriction_ids=_parse_restriction_ids(args.restrictions),
    )
    print(f"✅ Aluno salvo: {student['full_name']} ({student['id']})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cadastra aluno com uma única foto")
    parser.add_argument("--id", help="ID/slug único do aluno (default: slugify do nome)")
    parser.add_argument("--name", help="Nome completo")
    parser.add_argument("--phone", help="Telefone do responsável")
    parser.add_argument("--image", help="Caminho da foto do aluno")
    parser.add_argument("--email", default="", help="E-mail")
    parser.add_argument("--notes", default="", help="Observações")
    parser.add_argument("--enrollment", default=None, help="Matrícula formal")
    parser.add_argument(
        "--class-id",
        dest="class_id",
        default=None,
        help="UUID da turma (consulte /api/classes para listar)",
    )
    parser.add_argument(
        "--restrictions",
        default=None,
        help="UUIDs de restrições separados por vírgula (consulte /api/dietary-restrictions)",
    )
    args = parser.parse_args()

    if args.name and args.phone and args.image:
        register_from_args(args)
    else:
        register_interactive()
