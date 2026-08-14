# School Face Presence POC

POC local-first para reconhecimento simultâneo de alunos, presença escolar, Control iD, responsáveis e cozinha. **Não é um produto certificado nem conformidade integral com LGPD.**

## Arquitetura e fonte de verdade

`OpenCV/câmera -> recognition_pipeline -> Flask -> SQLite local -> outbox -> PostgreSQL central`.
SQLite é a fonte operacional da unidade: a portaria continua registrando eventos sem internet. PostgreSQL recebe cópias idempotentes para consolidação; nunca decide a abertura local. `event_key` é único no local e chave primária no central. Cadastros administrativos também são locais nesta POC; não há sincronização bidirecional.

## Pré-requisitos

- Python 3.11–3.13 (dlib 19.24.6; Python 3.14 não é suportado pelo stack de câmera)
- Linux/macOS, CMake e compilador C++ para dlib
- câmera USB/IP; PostgreSQL 14+ é opcional

## Instalação limpa

```bash
git clone https://github.com/RenanCOliveira93/face-detection-mvp.git
cd face-detection-mvp
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m unittest discover -s tests -v
```

Configure `ADMIN_BOOTSTRAP_TOKEN` com 32+ bytes aleatórios, `MOCK_MESSAGES=true`, timezone, câmera e, opcionalmente, `POSTGRES_DSN`. Inicie com `python main.py`; consulte `curl http://127.0.0.1:5000/api/status`.

## Provisionamento e fluxo reproduzível

```bash
# Primeira escola + administrador (as chaves só aparecem na criação)
curl -sS -X POST localhost:5000/api/schools -H 'Content-Type: application/json' \
 -H 'X-Bootstrap-Token: SEU_BOOTSTRAP' \
 -d '{"name":"Escola POC","slug":"escola-poc","timezone":"America/Sao_Paulo","admin_name":"Admin","admin_email":"admin@example.test"}'
export SCHOOL_KEY='api_key do admin retornado'

# Professor e turma
curl -X POST localhost:5000/api/school/members -H "X-School-Key: $SCHOOL_KEY" -H 'Content-Type: application/json' \
 -d '{"full_name":"Professora Ana","email":"ana@example.test","role":"professor"}'
curl -X POST localhost:5000/api/classes -H "X-School-Key: $SCHOOL_KEY" -H 'Content-Type: application/json' \
 -d '{"name":"5 A","school_year":"2026"}'

# Aluno/responsável: o telefone informado é migrado para o contato de responsável
curl -X POST localhost:5000/api/register -H "X-School-Key: $SCHOOL_KEY" \
 -F 'name=Aluno POC' -F 'phone=5511999999999' -F 'id=aluno-poc' -F 'image=@foto-com-consentimento.jpg'
curl -X POST localhost:5000/api/school/classrooms/1/students -H "X-School-Key: $SCHOOL_KEY" \
 -H 'Content-Type: application/json' -d '{"face_id":"aluno-poc"}'

# Restrição e nota (nota deve usar a chave retornada ao criar o professor)
curl -X POST localhost:5000/api/dietary-restrictions -H "X-School-Key: $SCHOOL_KEY" \
 -H 'Content-Type: application/json' -d '{"face_id":"aluno-poc","description":"Alergia a amendoim","severity":"alta"}'
curl -X POST localhost:5000/api/school/notes -H 'X-School-Key: CHAVE_PROFESSOR' \
 -H 'Content-Type: application/json' -d '{"face_id":"aluno-poc","body":"Participou da atividade"}'

# Dashboard e cozinha mock
curl localhost:5000/api/school/dashboard -H "X-School-Key: $SCHOOL_KEY"
curl -X POST localhost:5000/api/kitchen/recipients -H "X-School-Key: $SCHOOL_KEY" \
 -H 'Content-Type: application/json' -d '{"name":"Cozinha","phone":"5511888888888"}'
curl -X POST localhost:5000/api/kitchen/dispatch -H "X-School-Key: $SCHOOL_KEY" -H 'Content-Type: application/json' -d '{}'
```

## Cenário mock com as fotos locais existentes

`storage/` é ignorado pelo Git por conter biometria. Portanto, copie ou mantenha as
fotos autorizadas apenas na sua máquina, em `storage/faces`. Cada arquivo deve ter
um único rosto e extensão JPG, JPEG, PNG, BMP ou WEBP. Em seguida:

```bash
python scripts/seed_mock_school.py
export DB_PATH=database/mock_school.db
python main.py
```

O seed cria uma escola, dois perfis, duas turmas, alunos fictícios (um por foto),
matrículas, uma restrição fictícia, uma anotação, cozinha e dispositivo Control iD
mock. As chaves ficam apenas em `database/mock_school_manifest.json`, também ignorado.
Ele não altera nem duplica as fotos. Para evitar apagar dados por engano, recusa-se
a sobrescrever um banco já existente.

Teste as APIs com a `admin_key` do manifesto:

```bash
curl localhost:5000/api/school/dashboard -H "X-School-Key: $SCHOOL_KEY"
curl localhost:5000/api/faces -H "X-School-Key: $SCHOOL_KEY"
```

Para simular presença sem câmera, use o `device_id`, `device_secret` e um ID de aluno
do manifesto em `/new_user_identified.fcgi`. Envie outro `event_id` para alternar
entrada/saída; repita o mesmo `event_id` para validar idempotência. Depois valide o
dashboard e o dispatch da cozinha em modo `MOCK_MESSAGES=true`.

## Control iD seguro

Cadastre o dispositivo por uma rotina administrativa usando `FaceDatabase.create_device`. O callback exige identidade e segredo do dispositivo, `event_id` único (anti-replay/idempotência) e aluno da mesma escola:

```bash
curl -X POST localhost:5000/new_user_identified.fcgi \
 -H 'X-Device-Id: portaria-1' -H 'X-Device-Secret: SEGREDO_DO_DISPOSITIVO' \
 -H 'Content-Type: application/json' \
 -d '{"event_id":"terminal-0001","user_id":"aluno-poc","event_at":"2026-08-11T12:00:00Z"}'
```

Use HTTPS, VLAN/allowlist no proxy e nunca exponha o Flask diretamente à internet. A resposta `access=granted` só ocorre após autenticação, vínculo escolar e deduplicação. A abertura elétrica real permanece responsabilidade do controlador/rede homologados.

## PostgreSQL e offline

Execute `python scripts/sync_outbox.py`. Na indisponibilidade, presença continua no SQLite e itens permanecem `pending`; no retorno, reexecute. O `ON CONFLICT` central previne duplicidade. Turmas/restrições são criadas localmente e funcionam offline, mas **não são enviadas ao central nesta POC**; somente eventos colocados explicitamente na outbox são sincronizados.

| Recurso | Sem PostgreSQL | Com PostgreSQL |
|---|---:|---:|
| reconhecimento/presença/Control iD | sim | sim + cópia central |
| mensagens/webhook | sim, conforme conectividade do provedor | igual |
| turmas, notas, restrições, cozinha mock | sim, local | local |
| consolidação multiunidade central | não | eventos de presença |

## Segurança, biometria e LGPD mínima

Finalidade limitada: controle de presença com consentimento verificável do responsável. Colete uma foto adequada, minimize acessos e defina retenção com a escola. Chaves são armazenadas como SHA-256; valores originais só são retornados ao provisionar. Use disco criptografado, backup cifrado/testado, HTTPS, rotação/revogação de chaves e logs sem PII. `DELETE /api/school/students/<id>` anonimiza PII/embedding, remove foto local e vínculos, preservando auditoria não identificada. Defina procedimento de incidente (isolar, preservar evidência, rotacionar, avaliar titulares/ANPD). O arquivo biométrico legado foi removido do estado atual, mas removê-lo do histórico remoto exigiria reescrita separada e autorização.

## Testes, rollback e troubleshooting

```bash
python -m unittest discover -s tests -v
python -m py_compile main.py database.py config.py integrations/webhook_client.py recognition_pipeline.py sync_service.py
python scripts/smoke_poc.py
git diff --check
```

Rollback: pare o serviço, faça backup de `database/faces.db`, restaure o commit/aplicação anterior e o backup compatível. Migrações SQLite são progressivas e não têm downgrade automático. Se câmera falhar, valide `CAMERA_INDEX`, permissões e iluminação. Se PostgreSQL falhar, consulte `sync_outbox.last_error`. `401` indica chave ausente/inválida; `403`, papel ou escola incorretos.

## Limitações e homologação física

Não há quantidade universal de rostos simultâneos: homologue CPU/GPU, resolução, distância, ângulo, iluminação, oclusão, falsos positivos/negativos e tempo de resposta. Teste queda de energia/rede, reinício, virada do dia/timezone, saída de emergência, acesso manual, câmera indisponível, Control iD/VLAN/HTTPS, WhatsApp homologado, backup/restauração e consentimento/retenção. Não use reconhecimento como único mecanismo para decisões disciplinares ou de segurança física.
