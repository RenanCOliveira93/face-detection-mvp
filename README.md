# Face Detection MVP para escola

Sistema simples de reconhecimento facial de alunos com **uma foto por aluno** e disparo de mensagem no **WhatsApp do responsável**.

## O que mudou

- Não existe mais etapa de treinamento com várias imagens.
- Cada aluno é cadastrado com uma única foto.
- No cadastro, a foto é vetorizada e o embedding fica salvo no SQLite.
- Na câmera, cada rosto detectado é comparado por similaridade com os embeddings cadastrados.
- O envio de mensagem foi simplificado para **Meta WhatsApp Cloud API** ou **modo mock**.
- Código legado de treino em lote / Evolution / Twilio deixou de ser o fluxo principal.

## Fluxo (entrada/saída + telemetria operacional)

1. Cadastrar aluno com nome, telefone do responsável e uma foto.
2. Salvar foto original em `storage/faces/`.
3. Salvar embedding facial no banco `database/faces.db`.
4. Ao detectar um rosto na webcam, calcular embedding do frame atual.
5. Comparar com os embeddings cadastrados.
6. Se a distância ficar abaixo do threshold (`RECOGNITION_TOLERANCE`), reconhecer o aluno e abrir uma trilha ativa de presença.
7. Registrar evento de **entrada** em memória para telemetria (`/api/presence_events`).
8. Se o aluno ficar sem detecção por alguns segundos, encerrar a trilha ativa e registrar evento de **saída**.
9. Atualizar o painel (`/api/status`) com `last_event_direction`, `last_event_at` e `active_tracks`.
10. Enviar: `Aluno NOME chegou na escola.` respeitando cooldown de mensagens.

## Configuração

Crie um `.env` com algo como:

```env
PORT=5000
CAMERA_INDEX=0
RECOGNITION_TOLERANCE=0.45
MESSAGE_COOLDOWN=60
FACE_IMAGES_DIR=storage/faces

# Para testes locais
MOCK_MESSAGES=true

# Para WhatsApp real pela Meta
USE_META_WHATSAPP=false
META_WHATSAPP_TOKEN=
META_PHONE_NUMBER_ID=
META_API_VERSION=v19.0
DEFAULT_RECIPIENT=
```

## Instalação

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Cadastrar um aluno

```bash
python scripts/register_face.py --name "Maria Souza" --phone "5511999999999" --image "/caminho/maria.jpg"
```

Se preferir, rode sem argumentos e responda interativamente:

```bash
python scripts/register_face.py
```

## Rodar a aplicação

```bash
python main.py
```

Abra `http://localhost:5000`.

## Testar o WhatsApp

```bash
python scripts/test_messaging.py
```

## Observações importantes

- A foto de cadastro deve conter **apenas um rosto**.
- O threshold ideal depende da câmera e iluminação; comece em `0.45`.
- Em `MOCK_MESSAGES=true`, nenhuma mensagem real é enviada.
- Para produção, o canal recomendado aqui é a **Meta WhatsApp Cloud API**.
