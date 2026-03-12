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


def main():
    print("\n" + "=" * 50)
    print("  Teste de Mensageria")
    print("=" * 50)

    recipient = CONFIG["default_recipient"]
    name      = "Usuário Demo"
    message   = f"{name} acabou de chegar aqui. 🟢"

    print(f"\n  Destinatário : +{recipient}")
    print(f"  Mensagem     : {message}")
    print(f"  Canal        : {'MOCK' if CONFIG['mock_messages'] else 'Evolution API / Twilio'}")
    print()

    ok, info = send_whatsapp_message(recipient, message)

    if ok:
        print(f"✅ Mensagem enviada com sucesso!")
        print(f"   Info: {info}")
    else:
        print(f"❌ Falha no envio: {info}")
        print("\nVerifique:")
        print("  • Evolution API rodando? (docker-compose up -d)")
        print("  • WhatsApp autenticado? (python scripts/setup_whatsapp.py)")
        print("  • Ou ative o mock: MOCK_MESSAGES=true no .env")


if __name__ == "__main__":
    main()
