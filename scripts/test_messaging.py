"""
Teste de envio de mensagem sem necessidade de câmera.

Uso:
  python scripts/test_messaging.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from messaging import send_whatsapp_message
from config import CONFIG


def get_active_channel() -> str:
    if CONFIG["mock_messages"]:
        return "MOCK (sem envio real)"
    if CONFIG["use_meta_whatsapp"]:
        return "Meta Cloud API (WhatsApp Business)"
    if CONFIG["use_twilio"]:
        return "Twilio SMS"
    return "Evolution API (Docker local)"


def main():
    print("\n" + "=" * 55)
    print("  Teste de Mensageria")
    print("=" * 55)

    recipient = CONFIG["default_recipient"]
    name      = "Usuário Demo"
    message   = f"👋 Olá! *{name}* acabou de ser reconhecido pelo sistema. 🟢"

    print(f"\n  Canal        : {get_active_channel()}")
    print(f"  Destinatário : +{recipient}")
    print(f"  Mensagem     : {message}")
    print()

    ok, info = send_whatsapp_message(recipient, message)

    if ok:
        print(f"✅ Mensagem enviada com sucesso!")
        print(f"   Info: {info}")
    else:
        print(f"❌ Falha no envio: {info}")
        print("\nVerifique:")
        if CONFIG["use_meta_whatsapp"]:
            print("  • META_WHATSAPP_TOKEN configurado no .env?")
            print("  • META_PHONE_NUMBER_ID configurado no .env?")
            print("  • Token ainda válido? (tokens temporários expiram em 24h)")
            print("  • Número destinatário verificado no sandbox da Meta?")
            print("  → Execute: python scripts/setup_meta_whatsapp.py")
        else:
            print("  • Evolution API rodando? (docker-compose up -d)")
            print("  • WhatsApp autenticado? (python scripts/setup_whatsapp.py)")
            print("  • Ou ative o mock: MOCK_MESSAGES=true no .env")


if __name__ == "__main__":
    main()
