# Face Detection MVP para escola

Sistema simples de reconhecimento facial de alunos com **uma foto por aluno** e disparo de mensagem no **WhatsApp do responsável**.

## O que mudou

- Não existe mais etapa de treinamento com várias imagens.
- Cada aluno é cadastrado com uma única foto.
- No cadastro, a foto é vetorizada e o embedding fica salvo no SQLite.
- Na câmera, cada rosto detectado é comparado por similaridade com os embeddings cadastrados.
- O envio de mensagem suporta **Meta WhatsApp Cloud API**, **Evolution API** ou **modo mock**.
- O roteador usa prioridade: `MOCK_MESSAGES` -> `USE_EVOLUTION_API` -> `USE_META_WHATSAPP`.

## Fluxo (entrada/saída + telemetria operacional)

1. Cadastrar aluno com nome, telefone do responsável e uma foto.
2. Salvar foto original em `storage/faces/`.
3. Salvar embedding facial no banco `database/faces.db`.
4. Ao detectar um rosto na webcam, calcular embedding do frame atual.
5. Comparar com os embeddings cadastrados.
6. Se a distância ficar abaixo do threshold (`RECOGNITION_TOLERANCE`), reconhecer o aluno e abrir uma trilha ativa de presença.
7. Registrar evento de **entrada** persistido no SQLite (`presence_events`).
8. Se o aluno ficar sem detecção por alguns segundos, encerrar a trilha ativa e registrar evento de **saída** persistido.
9. Atualizar o painel (`/api/status`) com `last_event_direction`, `last_event_at`, `active_tracks` e resumo por aluno.
10. Enviar mensagens de **entrada e saída** (`chegou` / `saiu`) com cooldown por direção (ex.: `aluno-1:entrada` e `aluno-1:saida`).
11. Auditar no próprio evento de presença os campos `message_ok`, `message_info` e `message_sent_at`.

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

# Para WhatsApp real via Evolution API
USE_EVOLUTION_API=false
EVOLUTION_BASE_URL=
EVOLUTION_API_KEY=
EVOLUTION_INSTANCE=

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


## Migrações de banco

O schema agora é versionado com `PRAGMA user_version` e migrações sequenciais automáticas na inicialização (`FaceDatabase`).
Isso evita divergência de colunas/tabelas em bancos legados e inclui a tabela `presence_events` para auditoria completa de entrada/saída.

## Validar Evolution em homologação

1. Configure no `.env`:

```env
MOCK_MESSAGES=false
USE_EVOLUTION_API=true
EVOLUTION_BASE_URL=http://localhost:8080
EVOLUTION_API_KEY=seu-token
EVOLUTION_INSTANCE=instancia-hml
```

2. Execute um disparo de teste sem enviar (validação de argumentos):

```bash
python scripts/send_evolution_message.py --phone 5511999999999 --message "Teste homologação" --dry-run
```

3. Execute o disparo real:

```bash
python scripts/send_evolution_message.py --phone 5511999999999 --message "Teste homologação"
```

4. Verifique no retorno os campos padronizados para auditoria: `success`, `provider`, `request_id`, `raw_response`.

