"""Script utilitário para disparo de mensagem via Evolution API."""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from messaging_evolution import send_via_evolution


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enviar mensagem pelo provider Evolution API")
    parser.add_argument("--phone", required=True, help="Telefone do destinatário (com ou sem máscara)")
    parser.add_argument("--message", required=True, help="Mensagem de texto")
    parser.add_argument("--dry-run", action="store_true", help="Somente valida argumentos sem enviar")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.dry_run:
        print("DRY-RUN: envio não realizado")
        print({"phone": args.phone, "message": args.message})
        return 0

    ok, result = send_via_evolution(args.phone, args.message)
    print("OK" if ok else "ERRO", result)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
