"""Teste manual do envio de WhatsApp."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CONFIG
from messaging import send_whatsapp_message


if __name__ == "__main__":
    recipient = CONFIG["default_recipient"] or input("Número do responsável: ").strip()
    ok, info = send_whatsapp_message(recipient, "Aluno Teste chegou na escola.")
    print("OK" if ok else "ERRO", info)
