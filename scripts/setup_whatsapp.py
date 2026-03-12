"""
Script de configuração da Evolution API (WhatsApp)
Cria a instância e exibe o QR Code para autenticação.

Pré-requisito: Evolution API rodando via Docker
  docker-compose up -d

Uso:
  python scripts/setup_whatsapp.py
"""

import sys
import os
import requests
import time
import qrcode
import io

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CONFIG


BASE_URL  = CONFIG["evolution_api_url"].rstrip("/")
API_KEY   = CONFIG["evolution_api_key"]
INSTANCE  = CONFIG["evolution_instance"]

HEADERS = {
    "Content-Type": "application/json",
    "apikey": API_KEY
}


def check_api():
    try:
        r = requests.get(f"{BASE_URL}/", headers=HEADERS, timeout=5)
        return r.status_code < 500
    except Exception:
        return False


def create_instance():
    payload = {
        "instanceName": INSTANCE,
        "qrcode": True,
        "integration": "WHATSAPP-BAILEYS"
    }
    r = requests.post(f"{BASE_URL}/instance/create",
                      headers=HEADERS, json=payload, timeout=10)
    return r.json()


def get_qrcode():
    r = requests.get(f"{BASE_URL}/instance/connect/{INSTANCE}",
                     headers=HEADERS, timeout=10)
    return r.json()


def get_status():
    r = requests.get(f"{BASE_URL}/instance/connectionState/{INSTANCE}",
                     headers=HEADERS, timeout=10)
    data = r.json()
    return data.get("instance", {}).get("state", "unknown")


def print_qr_terminal(qr_base64: str):
    """Exibe QR Code no terminal."""
    import base64
    from PIL import Image

    try:
        img_data = base64.b64decode(qr_base64.split(",")[-1])
        img = Image.open(io.BytesIO(img_data))
        img.save("/tmp/whatsapp_qr.png")
        print("\n📱 QR Code salvo em /tmp/whatsapp_qr.png")
        print("   Abra o arquivo e escaneie com o WhatsApp do número remetente\n")
    except Exception as e:
        print(f"  QR Base64 (cole num decoder): {qr_base64[:100]}...")


def main():
    print("\n" + "=" * 55)
    print("  WhatsApp Setup via Evolution API")
    print("=" * 55)

    # 1. Verificar se API está online
    if not check_api():
        print("\n❌ Evolution API não está acessível em:", BASE_URL)
        print("\nPara iniciar, execute na raiz do projeto:")
        print("  docker-compose up -d\n")
        return

    print(f"\n✅ Evolution API online: {BASE_URL}")

    # 2. Criar instância
    print(f"\n[1/3] Criando instância '{INSTANCE}'...")
    try:
        result = create_instance()
        print(f"  Resultado: {result.get('instance', {}).get('instanceName', '?')}")
    except Exception as e:
        print(f"  (Instância pode já existir: {e})")

    # 3. Obter QR Code
    print("\n[2/3] Obtendo QR Code...")
    try:
        qr_data = get_qrcode()
        qr_code = qr_data.get("base64") or qr_data.get("qrcode", {}).get("base64", "")
        if qr_code:
            print_qr_terminal(qr_code)
        else:
            print("  QR não disponível (instância pode já estar conectada)")
    except Exception as e:
        print(f"  Erro ao obter QR: {e}")

    # 4. Aguardar conexão
    print("\n[3/3] Aguardando autenticação WhatsApp...")
    print("  (Escaneie o QR Code com o número:", CONFIG["sender_phone"], ")")

    for i in range(30):
        time.sleep(3)
        status = get_status()
        print(f"  [{i*3}s] Status: {status}")
        if status == "open":
            print("\n✅ WhatsApp conectado com sucesso!")
            print(f"  Remetente: {CONFIG['sender_phone']}")
            break
    else:
        print("\n⚠️  Timeout — verifique o QR e tente novamente.")

    print("\n  Para enviar mensagem de teste execute:")
    print("  python scripts/test_messaging.py\n")


if __name__ == "__main__":
    main()
