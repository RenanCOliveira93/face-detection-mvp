"""
Script de configuração da Meta Cloud API (WhatsApp Business)
Verifica credenciais e envia uma mensagem de teste.

Sem Docker, sem servidor local — usa a API oficial da Meta.

─────────────────────────────────────────────────────────────
COMO OBTER AS CREDENCIAIS (passo a passo):
─────────────────────────────────────────────────────────────

1. Acesse: https://developers.facebook.com/apps/
2. Clique em "Criar App" → tipo "Business"
3. No painel do App, clique em "Adicionar produto" → WhatsApp
4. Vá em WhatsApp → Configuração da API
5. Copie:
   • "Token de acesso temporário" → META_WHATSAPP_TOKEN
   • "ID do número de telefone"   → META_PHONE_NUMBER_ID
6. Cole no arquivo .env do projeto

NÚMERO DE TESTE (sandbox):
  - A Meta fornece um número de teste gratuito
  - Você pode enviar para até 5 números verificados no sandbox
  - Para produção, adicione seu próprio número

Uso:
  python scripts/setup_meta_whatsapp.py
─────────────────────────────────────────────────────────────
"""

import sys
import os
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CONFIG


def check_credentials():
    """Verifica se as credenciais estão configuradas."""
    token = CONFIG["meta_whatsapp_token"]
    phone_id = CONFIG["meta_phone_number_id"]

    print("\n" + "=" * 60)
    print("  WhatsApp Business — Meta Cloud API — Setup")
    print("=" * 60)

    if not token:
        print("\n❌ META_WHATSAPP_TOKEN não configurado no .env")
        print_instructions()
        return False

    if not phone_id:
        print("\n❌ META_PHONE_NUMBER_ID não configurado no .env")
        print_instructions()
        return False

    print(f"\n✅ Token     : {token[:12]}...{token[-4:]} (configurado)")
    print(f"✅ Phone ID  : {phone_id}")
    print(f"✅ API versão: {CONFIG['meta_api_version']}")
    return True


def verify_token():
    """Verifica se o token é válido consultando a API da Meta."""
    token = CONFIG["meta_whatsapp_token"]
    phone_id = CONFIG["meta_phone_number_id"]
    version = CONFIG["meta_api_version"]

    url = f"https://graph.facebook.com/{version}/{phone_id}"
    headers = {"Authorization": f"Bearer {token}"}

    print("\n[1/2] Verificando token na API da Meta...")
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()

        if resp.status_code == 200:
            display_name = data.get("display_phone_number", "?")
            verified     = data.get("verified_name", "?")
            quality      = data.get("quality_rating", "?")
            print(f"  ✅ Token válido!")
            print(f"  📱 Número    : {display_name}")
            print(f"  🏷️  Nome      : {verified}")
            print(f"  ⭐ Qualidade : {quality}")
            return True
        else:
            error = data.get("error", {})
            print(f"  ❌ Token inválido: {error.get('message', resp.text)}")
            print(f"     Código: {error.get('code', '?')}")
            return False
    except Exception as e:
        print(f"  ❌ Erro ao verificar: {e}")
        return False


def send_test_message():
    """Envia mensagem de teste para o número destinatário padrão."""
    from messaging import send_via_meta

    recipient = CONFIG["default_recipient"]
    message = (
        "✅ *Face Recognition MVP* — Teste de integração\n\n"
        "WhatsApp Business (Meta Cloud API) configurado com sucesso! 🎉\n"
        "O sistema de reconhecimento facial está pronto para enviar alertas."
    )

    print(f"\n[2/2] Enviando mensagem de teste...")
    print(f"  Destinatário: +{recipient}")
    print(f"  Mensagem    : {message[:60]}...")

    ok, info = send_via_meta(recipient, message)

    if ok:
        print(f"\n✅ Mensagem enviada com sucesso!")
        print(f"   ID: {info}")
    else:
        print(f"\n❌ Falha no envio: {info}")
        if "not a valid whatsapp number" in str(info).lower():
            print("\n⚠️  O número destinatário não está no WhatsApp.")
            print("   Verifique DEFAULT_RECIPIENT no .env")
        elif "not verified" in str(info).lower() or "recipient" in str(info).lower():
            print("\n⚠️  No modo sandbox, o número destinatário precisa ser")
            print("   verificado no painel da Meta antes de receber mensagens.")
            print("   Acesse: https://developers.facebook.com/apps/ → WhatsApp → Configuração da API")


def print_instructions():
    print("""
─────────────────────────────────────────────────────────────
COMO CONFIGURAR (passo a passo):
─────────────────────────────────────────────────────────────

1. Acesse: https://developers.facebook.com/apps/
2. Crie um App → tipo "Business"
3. Adicione o produto "WhatsApp"
4. Vá em: WhatsApp → Configuração da API
5. Copie os valores e cole no .env:

   META_WHATSAPP_TOKEN=<seu token aqui>
   META_PHONE_NUMBER_ID=<seu phone number id aqui>
   USE_META_WHATSAPP=true

6. Execute novamente: python scripts/setup_meta_whatsapp.py
─────────────────────────────────────────────────────────────
""")


def main():
    if not check_credentials():
        return

    token_ok = verify_token()

    if token_ok:
        resp = input("\nDeseja enviar uma mensagem de teste? (s/N): ").strip().lower()
        if resp == "s":
            send_test_message()
        else:
            print("\nPulando teste de envio.")
    else:
        print("\n⚠️  Corrija o token antes de continuar.")
        print_instructions()

    print("\n  Para enviar mensagem de teste a qualquer momento:")
    print("  python scripts/test_messaging.py\n")


if __name__ == "__main__":
    main()
