# 🎥 Face Recognition MVP

Sistema de reconhecimento facial com notificação via **WhatsApp** para controle de acesso.

> **Stack**: Python · OpenCV · face_recognition · Flask · Evolution API (WhatsApp open-source) · SQLite

---

## Arquitetura

```
┌─────────────────────────────────────────────────────┐
│                    FLUXO PRINCIPAL                   │
│                                                     │
│  Webcam ──→ OpenCV ──→ face_recognition             │
│               │              │                      │
│            Detecta        Compara                   │
│            rostos         encodings                 │
│               │              │                      │
│            ┌──┴──────────────┴──┐                   │
│            │                   │                    │
│        CONHECIDO           DESCONHECIDO             │
│            │                   │                    │
│      Overlay verde       Overlay vermelho            │
│      + nome na tela      + banner ALERTA             │
│            │                                        │
│    Envia WhatsApp via                               │
│    Evolution API (Docker)                           │
│    "[Nome] acabou de chegar aqui. 🟢"               │
└─────────────────────────────────────────────────────┘
```

```
face_recognition_mvp/
├── main.py                  # Aplicação Flask (servidor + streaming)
├── config.py                # Configurações centrais
├── database.py              # SQLite (perfis de rostos + histórico)
├── messaging.py             # Envio WhatsApp / SMS / Mock
├── docker-compose.yml       # Evolution API (WhatsApp)
├── requirements.txt
├── .env.example             # ← copie para .env
├── templates/
│   └── index.html           # Interface web
├── training_images/         # Fotos de treinamento por pessoa
│   └── <face_id>/
│       ├── foto1.jpg
│       └── ...
├── models/
│   └── face_encodings.pkl   # Modelo treinado (gerado automaticamente)
└── scripts/
    ├── register_face.py     # Cadastrar novo rosto
    ├── train_model.py       # Treinar modelo com as fotos
    ├── demo_setup.py        # Setup rápido para demo
    ├── setup_whatsapp.py    # Autenticar Evolution API
    └── test_messaging.py    # Testar envio de mensagem
```

---

## ⚡ Setup Rápido (5 minutos)

### 1. Pré-requisitos

```bash
# Python 3.10+
# Docker + Docker Compose
# Webcam conectada

# Dependências do sistema (Ubuntu/Debian)
sudo apt-get install -y cmake build-essential libgtk2.0-dev

# Dependências do sistema (macOS)
brew install cmake
```

### 2. Instalar dependências Python

```bash
# Criar ambiente virtual (recomendado)
python -m venv venv
source venv/bin/activate      # Linux/Mac
# venv\Scripts\activate       # Windows

pip install -r requirements.txt
```

> ⚠️ A instalação do `face-recognition` (dlib) pode levar alguns minutos.

### 3. Configurar variáveis de ambiente

```bash
cp .env.example .env
# Edite o .env se necessário (os valores padrão já estão configurados)
```

### 4. Cadastrar um rosto

```bash
python scripts/register_face.py
```

Preencha: nome, ID, telefone. Exemplo:
```
Nome completo  : João Silva
ID do rosto    [joao_silva]: (Enter)
Telefone       : 5514997283283
```

### 5. Adicionar fotos de treinamento

Copie **5 a 20 fotos** da pessoa para:
```
training_images/joao_silva/
├── foto1.jpg
├── foto2.jpg
└── ...
```

Ou use o script de captura pela webcam:
```bash
python scripts/demo_setup.py
```

### 6. Treinar o modelo

```bash
python scripts/train_model.py
```

### 7. Configurar WhatsApp (Evolution API)

```bash
# Subir o container Docker
docker-compose up -d

# Aguardar ~10 segundos e autenticar
python scripts/setup_whatsapp.py
```

Escaneie o QR Code com o WhatsApp do número **remetente** (5514998338034).

### 8. Iniciar a aplicação

```bash
python main.py
```

Acesse: **http://localhost:5000**

---

## 🧪 Modo de Teste (sem WhatsApp)

Para testar sem configurar o WhatsApp, ative o modo mock:

```bash
# No .env:
MOCK_MESSAGES=true

# Ou na linha de comando:
MOCK_MESSAGES=true python main.py
```

As mensagens serão exibidas no terminal em vez de enviadas.

---

## 📱 Canais de Mensagem

| Canal | Configuração | Observação |
|-------|-------------|------------|
| **Evolution API** (padrão) | Docker local | WhatsApp real, gratuito |
| **Twilio SMS** | `USE_TWILIO=true` + credenciais | Pago, mas simples |
| **Mock** | `MOCK_MESSAGES=true` | Apenas log no terminal |

### Configurar Twilio (alternativo)

1. Criar conta em [twilio.com](https://www.twilio.com) (trial gratuito)
2. Obter `Account SID` e `Auth Token`
3. Configurar no `.env`:
```env
USE_TWILIO=true
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxx
TWILIO_FROM=+1xxxxxxxxxx
```

---

## 🎛️ Configurações

| Parâmetro | Padrão | Descrição |
|-----------|--------|-----------|
| `RECOGNITION_TOLERANCE` | `0.50` | Sensibilidade (0.4=rigoroso, 0.6=permissivo) |
| `PORT` | `5000` | Porta do servidor |
| `SENDER_PHONE` | `5514998338034` | Número remetente |
| `DEFAULT_RECIPIENT` | `5514997283283` | Destinatário padrão |

---

## 🔍 API Endpoints

| Método | Rota | Descrição |
|--------|------|-----------|
| `GET` | `/` | Interface web |
| `GET` | `/video_feed` | Stream MJPEG da câmera |
| `GET` | `/api/status` | Status JSON em tempo real |
| `POST` | `/api/reload_model` | Recarregar modelo sem reiniciar |

---

## 🛠️ Troubleshooting

**Câmera não abre:**
```bash
# Verifique o índice da câmera (0, 1, 2...)
# Edite main.py: cv2.VideoCapture(0) → cv2.VideoCapture(1)
```

**Rosto não reconhecido:**
- Adicione mais fotos (diferentes ângulos e iluminações)
- Aumente a tolerância: `RECOGNITION_TOLERANCE=0.60`
- Retreine: `python scripts/train_model.py`

**Evolution API não conecta:**
```bash
docker-compose logs evolution_api
# Verifique se a porta 8080 está livre
```

**Falsos negativos / positivos:**
- `0.45` → mais restritivo (menos falsos positivos)
- `0.55` → mais permissivo (menos falsos negativos)
