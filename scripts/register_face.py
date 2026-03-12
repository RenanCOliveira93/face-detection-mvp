"""
Script de Cadastro de Rostos
Registra um novo perfil no banco de dados e cria a pasta de treinamento.

Uso:
  python scripts/register_face.py
  python scripts/register_face.py --id joao_silva --name "João Silva" --phone "5514999990000"
"""

import sys
import os
import argparse
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import FaceDatabase
from config import CONFIG
from pathlib import Path


def slugify(text: str) -> str:
    """Converte nome para slug simples (sem acentos, espaços → _)."""
    import unicodedata
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[\s-]+", "_", text)


def register_interactive():
    print("\n" + "=" * 50)
    print("  Cadastro de Novo Rosto")
    print("=" * 50)

    db = FaceDatabase()

    full_name = input("\n  Nome completo : ").strip()
    if not full_name:
        print("❌ Nome obrigatório.")
        return

    suggested_id = slugify(full_name)
    face_id_input = input(f"  ID do rosto   [{suggested_id}]: ").strip()
    face_id = face_id_input if face_id_input else suggested_id

    # Verificar se já existe
    if db.get_face(face_id):
        print(f"❌ ID '{face_id}' já cadastrado.")
        overwrite = input("  Atualizar dados? (s/N): ").strip().lower()
        if overwrite != "s":
            return

    phone = input("  Telefone (c/ DDD, ex: 5514999990000): ").strip()
    if not phone:
        print("❌ Telefone obrigatório.")
        return

    email = input("  E-mail (opcional): ").strip()
    notes = input("  Notas (opcional) : ").strip()

    # Salvar no banco
    ok = db.add_face(face_id, full_name, phone, email, notes)

    if not ok:
        # Atualizar se já existe
        db.update_face(face_id, full_name=full_name, phone=phone, email=email, notes=notes)
        print(f"\n✅ Perfil de '{full_name}' atualizado com ID: {face_id}")
    else:
        print(f"\n✅ Perfil de '{full_name}' cadastrado com ID: {face_id}")

    # Criar pasta de treinamento
    training_path = Path(CONFIG["training_images_dir"]) / face_id
    training_path.mkdir(parents=True, exist_ok=True)

    print(f"\n📁 Adicione fotos do rosto de '{full_name}' em:")
    print(f"   {training_path.resolve()}")
    print("\n   Dicas para melhores resultados:")
    print("   • Mínimo 5 fotos, idealmente 10–20")
    print("   • Diferentes ângulos (frente, 3/4, lateral)")
    print("   • Diferentes iluminações")
    print("   • Formato: JPG, PNG ou WEBP")
    print("\n   Depois execute: python scripts/train_model.py\n")


def register_from_args(args):
    db = FaceDatabase()
    face_id = args.id or slugify(args.name)
    ok = db.add_face(face_id, args.name, args.phone,
                     getattr(args, "email", ""),
                     getattr(args, "notes", ""))
    if ok:
        Path(CONFIG["training_images_dir"], face_id).mkdir(parents=True, exist_ok=True)
        print(f"✅ '{args.name}' cadastrado com ID '{face_id}'")
    else:
        print(f"❌ Falha ao cadastrar (ID '{face_id}' pode já existir)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cadastra um rosto no sistema")
    parser.add_argument("--id",    help="ID único do rosto")
    parser.add_argument("--name",  help="Nome completo")
    parser.add_argument("--phone", help="Telefone (com DDI/DDD)")
    parser.add_argument("--email", help="E-mail", default="")
    parser.add_argument("--notes", help="Notas", default="")

    args = parser.parse_args()

    if args.name and args.phone:
        register_from_args(args)
    else:
        register_interactive()
