-- Migration 001. Everything Phase A needs and nothing it does not.
-- Phase B adds knowledge, verification codes and bookings. Phase C adds calls.
--
-- Two decisions worth knowing before you change anything here:
--   1. Every table carries tenant_id. Adding it later is painful and you onboard client two
--      in week 7, not week 40.
--   2. turns.idempotency_key is UNIQUE. Meta re-delivers webhooks for up to 72 hours. Without
--      this, a retry books the guest twice.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE tenants (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name          TEXT NOT NULL,
  country       TEXT NOT NULL,                     -- AE | EG | GB
  timezone      TEXT NOT NULL DEFAULT 'Asia/Dubai',
  default_language TEXT NOT NULL DEFAULT 'en',
  status        TEXT NOT NULL DEFAULT 'active',    -- active | paused | churned
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- How customers can reach this client. One row per channel per client.
CREATE TABLE channels (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  kind            TEXT NOT NULL,                   -- whatsapp | voice | web | email | instagram
  external_id     TEXT NOT NULL,                   -- WhatsApp phone number id, or the phone number
  config          JSONB NOT NULL DEFAULT '{}',     -- licence numbers, greeting, opening hours
  enabled         BOOLEAN NOT NULL DEFAULT true,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (kind, external_id)
);

-- Who we are talking to. Carried over from v1.2 almost unchanged.
CREATE TABLE contacts (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  full_name     TEXT,
  phone_e164    TEXT NOT NULL,
  email         TEXT,
  alternate_phones TEXT[] NOT NULL DEFAULT '{}',
  external_system TEXT,                            -- hostaway | guesty | hubspot | sheets
  external_id   TEXT,
  last_seen_at  TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, phone_e164)
);
CREATE INDEX idx_contacts_phone ON contacts (tenant_id, phone_e164);
CREATE INDEX idx_contacts_name_fuzzy ON contacts USING gin (full_name gin_trgm_ops);

-- One conversation. Groups turns and holds whether we have proved who we are talking to.
CREATE TABLE conversations (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  channel           TEXT NOT NULL,
  channel_thread_id TEXT NOT NULL,
  contact_id        UUID REFERENCES contacts(id),
  identity_verified BOOLEAN NOT NULL DEFAULT false,
  language          TEXT,
  status            TEXT NOT NULL DEFAULT 'open',  -- open | resolved | handed_off | abandoned
  started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_turn_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  ended_at          TIMESTAMPTZ
);
CREATE INDEX idx_conv_open ON conversations (tenant_id, status, last_turn_at DESC);
CREATE UNIQUE INDEX idx_conv_active_thread
  ON conversations (tenant_id, channel, channel_thread_id)
  WHERE status = 'open';

-- Every message in and out. The unique key is what stops double handling.
CREATE TABLE turns (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id   UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  tenant_id         UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  direction         TEXT NOT NULL,                 -- inbound | outbound
  channel           TEXT NOT NULL,
  modality          TEXT NOT NULL,                 -- text | audio | keypad | button
  body_text         TEXT,
  speech_confidence REAL,
  idempotency_key   TEXT NOT NULL,
  raw_payload       JSONB NOT NULL DEFAULT '{}',
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (idempotency_key)
);
CREATE INDEX idx_turns_conv ON turns (conversation_id, created_at);

-- The job in progress and the blanks still to fill.
CREATE TABLE tasks (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  intent          TEXT NOT NULL,
  slots           JSONB NOT NULL DEFAULT '{}',
  slots_confirmed JSONB NOT NULL DEFAULT '[]',
  status          TEXT NOT NULL DEFAULT 'collecting',
  outcome_ref     TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_tasks_open ON tasks (tenant_id, status, updated_at DESC);

-- What the model understood. Mirrors the Understanding model field for field.
CREATE TABLE understandings (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  turn_id            UUID NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
  tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  task_id            UUID REFERENCES tasks(id),
  intent             TEXT NOT NULL,
  slots              JSONB NOT NULL DEFAULT '{}',
  person_name        TEXT,
  language           TEXT NOT NULL,
  summary_one_line   TEXT NOT NULL,
  confidence_overall REAL NOT NULL,
  confidence_intent  REAL NOT NULL,
  confidence_slots   REAL NOT NULL,
  confidence_band    TEXT NOT NULL,                -- high | medium | low
  autonomy           TEXT NOT NULL,                -- act | act_and_notify | hand_off
  model_used         TEXT NOT NULL,
  prompt_version     TEXT NOT NULL,
  latency_ms         INTEGER,
  raw_json           JSONB NOT NULL,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_und_review ON understandings (tenant_id, confidence_band, created_at DESC);

-- Append only. Never updated, never deleted. This is what you show a client when they ask
-- what the receptionist did and why.
CREATE TABLE audit_log (
  id               BIGSERIAL PRIMARY KEY,
  tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  conversation_id  UUID REFERENCES conversations(id),
  turn_id          UUID REFERENCES turns(id),
  task_id          UUID REFERENCES tasks(id),
  actor            TEXT NOT NULL,                  -- receptionist | human | system
  action           TEXT NOT NULL,                  -- the tool name, or replied | handed_off
  decision_reason  TEXT,                           -- why it was allowed to do this
  payload          JSONB,
  outcome          TEXT NOT NULL,                  -- success | failed | skipped
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_time ON audit_log (tenant_id, created_at DESC);

-- Free training data. When a human fixes what the receptionist got wrong, that is a labelled
-- example. v1.2 called this corrections and the idea carries over whole.
CREATE TABLE corrections (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  understanding_id  UUID NOT NULL REFERENCES understandings(id) ON DELETE CASCADE,
  tenant_id         UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  original_json     JSONB NOT NULL,
  corrected_json    JSONB NOT NULL,
  corrected_via     TEXT NOT NULL,                 -- control_page | whatsapp_ping
  promoted_to_golden BOOLEAN NOT NULL DEFAULT false,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- What every call and message actually cost. Without this you cannot answer "did I make money
-- on this client last month" and you cannot enforce the overage charges in the price list.
CREATE TABLE usage_events (
  id          BIGSERIAL PRIMARY KEY,
  tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  period      DATE NOT NULL,
  metric      TEXT NOT NULL,                       -- call_minutes | conversations | otp | template
  quantity    NUMERIC(12,4) NOT NULL,
  unit_cost   NUMERIC(10,6),
  ref_id      UUID,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_usage_period ON usage_events (tenant_id, period, metric);

-- Accuracy over time. Answers "did it get worse after I changed the prompt?" from SQL alone.
CREATE TABLE eval_runs (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  prompt_version    TEXT NOT NULL,
  model             TEXT NOT NULL,
  golden_set_size   INTEGER NOT NULL,
  intent_accuracy   REAL NOT NULL,
  slot_accuracy     JSONB NOT NULL,                -- per detail: name, date, phone, unit
  per_language      JSONB NOT NULL,
  autonomy_safety   JSONB NOT NULL,                -- how often it acted when it should not have
  git_sha           TEXT,
  tenant_id         UUID REFERENCES tenants(id),   -- null means the shared question set
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
