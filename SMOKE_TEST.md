# Smoke Test — TeyaTech edge (Sprint 1)

End-to-end manual test you can run today on your dev laptop (Mac or Windows/Linux,
CPU only) against the real Supabase project. Each step has a checkbox so you can
run through it before fechar `main`.

---

## Pré-requisitos

- Python 3.11+
- Webcam funcionando (`CAMERA_INDEX=0`)
- Acesso ao Supabase project (URL + service_role key)
- Migration `20260528120000_multi_school_schema.sql` aplicada no Supabase
- Modelos InsightFace baixados na primeira execução (~50MB para `buffalo_sc`)

---

## 1 — Setup local

```bash
cd face-detection-mvp
python -m venv .venv && source .venv/bin/activate   # ou .venv\Scripts\activate no Windows
pip install -r requirements.txt
cp .env.example .env
```

Edite o `.env`:

- [ ] `SUPABASE_URL=https://ylcemxzdkdymsokgrrun.supabase.co`
- [ ] `SUPABASE_SERVICE_KEY=<service_role do dashboard>`
- [ ] `SUPABASE_DISABLED=false`
- [ ] Provisione uma escola de teste no Supabase (via SQL Editor, por enquanto):

```sql
WITH ins_school AS (
  INSERT INTO schools (name, slug, timezone)
  VALUES ('Escola POC', 'escola-poc', 'America/Sao_Paulo')
  RETURNING id
),
ins_settings AS (
  INSERT INTO school_settings (school_id) SELECT id FROM ins_school
  RETURNING school_id
),
ins_device AS (
  INSERT INTO devices (school_id, name, api_key_hash, status)
  SELECT id, 'laptop-test', encode(gen_random_bytes(32), 'hex'), 'active' FROM ins_school
  RETURNING id, school_id
)
SELECT
  (SELECT id FROM ins_school) AS school_id,
  (SELECT id FROM ins_device) AS device_id;
```

- [ ] Cole o `school_id` retornado em `SCHOOL_ID=` no `.env`
- [ ] Cole o `device_id` retornado em `DEVICE_ID=` no `.env`

---

## 2 — Boot do edge

```bash
python main.py
```

**Esperado nos logs:**

- [ ] `Inference providers (auto): ['CPUExecutionProvider']` (ou CoreML no Mac)
- [ ] `Loading InsightFace pack=buffalo_sc providers=... det_size=(640, 640)`
- [ ] `Inference backend ready: kind=onnx model=insightface-buffalo_sc-512 dim=512`
- [ ] `Supabase client ready (url=...)` (ou warning se cloud desabilitada)
- [ ] `reconcile_students: synced 0 students from Supabase` (primeira execução)
- [ ] `OutboxWorker started (poll=5.0s)`
- [ ] `HeartbeatWorker started (interval=30.0s)`
- [ ] Servindo em `http://0.0.0.0:5000`

**Validação cloud:**

- [ ] No painel Supabase, `devices.last_seen_at` da sua linha de teste foi atualizado nos últimos 60s.

---

## 3 — Benchmark CPU

```bash
python scripts/benchmark_inference.py --images storage/faces --iterations 30
```

(Se ainda não tem imagens em `storage/faces`, baixe 2–3 fotos com 1 rosto cada.)

**Esperado:**

- [ ] FPS > 5 em laptop modesto, > 15 em laptop bom
- [ ] Latência p95 < 200ms em laptop bom (pode ser maior em low-end)
- [ ] Avg faces/frame == número real de pessoas nas fotos

Anote os números em `BENCHMARK_BASELINE.md` (a criar quando você rodar).

---

## 4 — Cadastro

Opção A — via CLI:

```bash
python scripts/register_face.py --name "Aluno Teste" --phone "5511999999999" --image ~/Pictures/teste.jpg
```

Opção B — via UI: acesse `http://localhost:5000`, formulário de cadastro.

**Esperado:**

- [ ] CLI imprime `✅ Aluno salvo`
- [ ] Log: `Cadastro sync ok: face_id=aluno_teste student_id=<uuid>.`
- [ ] No Supabase: `students` tem 1 linha com `school_id` correto
- [ ] No Supabase: `student_embeddings` tem 1 linha com `is_current=true`, `model_version=insightface-buffalo_sc-512`, embedding 512-d

---

## 5 — Reconhecimento ao vivo

- [ ] Abrir `http://localhost:5000` no browser
- [ ] Permitir webcam (na verdade o Flask já lê direto via OpenCV — basta ter câmera plugada)
- [ ] Posicionar o rosto do aluno cadastrado
- [ ] Em poucos segundos a UI deve mostrar `RECONHECIDO: Aluno Teste`
- [ ] Log: `_handle_recognized` chamado, evento gravado

---

## 6 — Outbox + sync de eventos

Após reconhecimento:

- [ ] `presence_events` no Supabase tem 1 linha nova (direction=entrada, score ≥ 0.55)
- [ ] `daily_attendance` (local SQLite) atualizada
- [ ] `state.recent_people` na resposta de `/api/status` mostra o aluno

**Teste offline:**

- [ ] Desligue o WiFi
- [ ] Reconheça o aluno novamente — evento entra na fila local (verifique no SQLite: `SELECT * FROM outbox WHERE status='pending'`)
- [ ] Religue o WiFi
- [ ] Em até 5s o outbox drena: `SELECT * FROM outbox WHERE status='sent' ORDER BY id DESC`

---

## 7 — Reconciliação reversa (cloud → edge)

- [ ] Insira um novo aluno **direto no Supabase** (SQL ou painel):

```sql
WITH ins AS (
  INSERT INTO students (school_id, face_id, full_name, phone)
  VALUES ('<SCHOOL_ID>', 'aluno_dois', 'Aluno Dois', '5511988888888')
  RETURNING id, school_id
)
INSERT INTO student_embeddings (student_id, school_id, model_version, embedding, is_current)
SELECT id, school_id, 'insightface-buffalo_sc-512',
       (SELECT embedding FROM student_embeddings WHERE is_current LIMIT 1),  -- copia de outro só pra ter um vetor
       true
FROM ins;
```

- [ ] Reinicie `python main.py`
- [ ] Log mostra `reconcile_students: synced 2 students from Supabase`
- [ ] `GET /api/faces` lista os dois alunos

---

## 8 — Critérios de fechamento de Sprint 1

Sprint 1 só fecha quando **todos** os checkboxes acima estão marcados, mais:

- [ ] Nenhum erro `face_recognition` ou `dlib` em qualquer log (foram removidos)
- [ ] `requirements.txt` não tem mais `dlib` nem `face-recognition`
- [ ] Outbox + heartbeat funcionam em rede instável
- [ ] `python scripts/benchmark_inference.py` roda e imprime números

Se algum item falhar, abra issue, **não** marque como pronto.

---

## Sprint 1.5 — Domínio escolar (turma, restrições, cozinha)

Adicione a migration `20260528160000_school_domain.sql` no Supabase **antes** de
prosseguir. Tudo abaixo assume `SCHOOL_ID` e `DEVICE_ID` já no `.env`.

## 9 — Criar turma + restrições + destinatário cozinha

```bash
# Criar 1 turma
curl -X POST http://localhost:5000/api/classes \
  -H 'Content-Type: application/json' \
  -d '{"name":"A","grade":"1º ano","year":2026,"shift":"manha"}'
# Anote o `id` retornado (UUID) — será o CLASS_ID nas próximas etapas.

# Criar 1 restrição alimentar
curl -X POST http://localhost:5000/api/dietary-restrictions \
  -H 'Content-Type: application/json' \
  -d '{"name":"Lactose","severity":"grave","description":"intolerância grave"}'
# Anote o `id` retornado — RESTRICTION_ID.

# Criar destinatário cozinha
curl -X POST http://localhost:5000/api/kitchen/recipients \
  -H 'Content-Type: application/json' \
  -d '{"name":"Cozinha Dona Maria","phone_e164":"5511999998888"}'

# Habilitar dispatch no school_settings (SQL direto no Supabase por enquanto):
#   UPDATE school_settings
#     SET kitchen_enabled = true,
#         kitchen_dispatch_time = '11:00:00',
#         kitchen_dispatch_shifts = '["manha"]'::jsonb
#     WHERE school_id = '<SEU SCHOOL_ID>';
```

Validações:

- [ ] `GET /api/classes` retorna a turma criada
- [ ] `GET /api/dietary-restrictions` retorna a restrição
- [ ] `GET /api/kitchen/recipients` retorna o destinatário

## 10 — Cadastro com turma + matrícula + restrição

```bash
python scripts/register_face.py \
  --name "Aluno Teste" \
  --phone "5511999999999" \
  --image ~/Pictures/teste.jpg \
  --enrollment "2026-001" \
  --class-id "<CLASS_ID>" \
  --restrictions "<RESTRICTION_ID>"
```

Validações:

- [ ] No Supabase: `students` tem `class_id` e `enrollment_number` preenchidos
- [ ] `student_dietary_restrictions` tem 1 linha ativa ligando o aluno à restrição
- [ ] `faces` no SQLite local também tem `class_id` (verificar com `sqlite3 database/faces.db "SELECT class_id,enrollment_number FROM faces"`)

## 11 — Reconhecimento + contagem por turma

- [ ] Reconheça o aluno na webcam (aparece evento "entrada")
- [ ] `GET /api/classes/presence` retorna a turma com `present_count: 1` e o aluno na lista
- [ ] Reconheça outros alunos da mesma turma e veja a contagem subir

## 12 — Dispatch da cozinha (manual + agendado)

**Manual** (a qualquer hora):

```bash
curl -X POST http://localhost:5000/api/kitchen/dispatch \
  -H 'Content-Type: application/json' \
  -d '{"shift":"manha"}'
```

Esperado:

- [ ] Resposta `200` com `status: "sent"`, `present_count`, `restrictions_count`, e `message`
- [ ] Recipiente do WhatsApp recebe a mensagem (ou mock_messages loga ela)
- [ ] Segunda chamada retorna `status: "skipped"`, `reason: "already dispatched today"` (idempotência)

**Agendado**:

- [ ] Configure `kitchen_dispatch_time` para 2 minutos no futuro
- [ ] Espere — o worker checa a cada 60s
- [ ] Confirma que a mensagem foi enviada no horário e `GET /api/kitchen/dispatches` lista a entrada

## 13 — Critérios de fechamento de Sprint 1.5

- [ ] Todos os 4 itens "❌" do PDF agora têm endpoint + comportamento testado
- [ ] Mensagem da cozinha inclui contagem total + restrições nominais
- [ ] Idempotência funciona (não dispara duas vezes no mesmo dia/turno)
- [ ] Cadastro com turma e restrição roda em modo offline (`SUPABASE_DISABLED=true`) — só local, sem crash
