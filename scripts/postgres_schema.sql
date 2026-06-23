-- ─────────────────────────────────────────────────────────────────────────────
-- TeyaTech — PostgreSQL master schema (reference DDL)
--
-- This bootstraps the cloud master that the edge talks to directly via psycopg
-- (replaces the previous Supabase project). The column set is derived from the
-- queries in face-detection-mvp/repositories/*.py — keep them in sync.
--
-- Run once against a fresh database:
--     psql "$DATABASE_URL" -f scripts/postgres_schema.sql
--
-- If you already have an equivalent Supabase schema, you can skip this and just
-- point the POSTGRES_* env vars at that database — the repositories only need
-- the table/column/view names below to match.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- for gen_random_uuid()
-- Optional: for vector similarity instead of double precision[] embeddings:
-- CREATE EXTENSION IF NOT EXISTS vector;

-- ── Schools & settings ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS schools (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    slug        text UNIQUE,
    timezone    text NOT NULL DEFAULT 'America/Sao_Paulo',
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS school_settings (
    school_id                 uuid PRIMARY KEY REFERENCES schools(id) ON DELETE CASCADE,
    kitchen_enabled           boolean NOT NULL DEFAULT false,
    kitchen_dispatch_time     text,                 -- 'HH:MM' (ISO time)
    kitchen_dispatch_shifts   jsonb NOT NULL DEFAULT '["manha"]'::jsonb,
    kitchen_message_template  text,
    whatsapp_provider         text
);

-- ── Classes (turmas) ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS classes (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id   uuid NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    name        text NOT NULL,
    grade       text,
    shift       text NOT NULL DEFAULT 'manha',       -- manha | tarde | noite | integral
    year        integer,
    active      boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_classes_school ON classes(school_id);

-- ── Students ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS students (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id          uuid NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    face_id            text NOT NULL,                -- edge slug / device user id
    full_name          text NOT NULL,
    phone              text,
    email              text,
    external_id        text,                         -- e.g. Control iD numeric user id
    enrollment_number  text,
    class_id           uuid REFERENCES classes(id) ON DELETE SET NULL,
    status             text NOT NULL DEFAULT 'active',
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (school_id, face_id)
);
CREATE INDEX IF NOT EXISTS idx_students_school_status ON students(school_id, status);
CREATE INDEX IF NOT EXISTS idx_students_external ON students(school_id, external_id);

-- ── Student face embeddings ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS student_embeddings (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id     uuid NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    school_id      uuid NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    model_version  text NOT NULL,
    embedding      double precision[] NOT NULL,      -- 512-d ArcFace, L2-normalized
    is_current     boolean NOT NULL DEFAULT true,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_embeddings_current
    ON student_embeddings(student_id, model_version, is_current);

-- ── Dietary restrictions ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dietary_restrictions (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id    uuid NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    name         text NOT NULL,
    severity     text NOT NULL DEFAULT 'atencao',    -- atencao | grave | critica
    description  text,
    active       boolean NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (school_id, name)
);

CREATE TABLE IF NOT EXISTS student_dietary_restrictions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id      uuid NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    restriction_id  uuid NOT NULL REFERENCES dietary_restrictions(id) ON DELETE CASCADE,
    notes           text,
    active          boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (student_id, restriction_id)
);

-- ── Devices (edge heartbeat) ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS devices (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id           uuid NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    name                text,
    last_seen_at        timestamptz,
    model_version       text,
    inference_provider  text,
    hardware_info       jsonb,
    created_at          timestamptz NOT NULL DEFAULT now()
);

-- ── Presence events ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS presence_events (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id        uuid NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    device_id        uuid REFERENCES devices(id) ON DELETE SET NULL,
    student_id       uuid NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    face_id          text,
    direction        text NOT NULL,                  -- entrada | saida
    event_at         timestamptz NOT NULL,
    match_score      double precision,
    message_ok       boolean,
    message_info     text,
    message_sent_at  timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_events_school_time ON presence_events(school_id, event_at);
CREATE INDEX IF NOT EXISTS idx_events_student_time ON presence_events(student_id, event_at);

-- ── Kitchen recipients ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS kitchen_recipients (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id   uuid NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    name        text NOT NULL,
    phone_e164  text NOT NULL,
    channel     text NOT NULL DEFAULT 'whatsapp',
    email       text,
    active      boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Views consumed by the edge (classes_repo.presence_today / kitchen dispatcher)
-- "Present today" = the student's most recent event today is an 'entrada'.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW class_presence_today AS
SELECT c.school_id,
       c.id    AS class_id,
       c.name  AS class_name,
       c.grade AS class_grade,
       c.shift AS class_shift,
       COUNT(le.student_id) AS present_count
FROM classes c
LEFT JOIN students s
       ON s.class_id = c.id AND s.status = 'active'
LEFT JOIN (
    SELECT DISTINCT ON (student_id) student_id, direction
    FROM presence_events
    WHERE event_at::date = CURRENT_DATE
    ORDER BY student_id, event_at DESC
) le ON le.student_id = s.id AND le.direction = 'entrada'
GROUP BY c.school_id, c.id, c.name, c.grade, c.shift;

CREATE OR REPLACE VIEW students_present_today_with_restrictions AS
SELECT s.school_id,
       s.id        AS student_id,
       s.full_name,
       c.shift     AS shift,
       COALESCE(
           json_agg(json_build_object('name', dr.name))
               FILTER (WHERE dr.id IS NOT NULL),
           '[]'::json
       ) AS restrictions
FROM students s
JOIN (
    SELECT DISTINCT ON (student_id) student_id, direction
    FROM presence_events
    WHERE event_at::date = CURRENT_DATE
    ORDER BY student_id, event_at DESC
) le ON le.student_id = s.id AND le.direction = 'entrada'
LEFT JOIN classes c ON c.id = s.class_id
LEFT JOIN student_dietary_restrictions sdr ON sdr.student_id = s.id AND sdr.active = true
LEFT JOIN dietary_restrictions dr ON dr.id = sdr.restriction_id AND dr.active = true
WHERE s.status = 'active'
GROUP BY s.school_id, s.id, s.full_name, c.shift;
