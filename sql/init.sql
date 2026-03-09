-- ============================================================
-- Annie Hogar Bot - Esquema inicial PostgreSQL
-- ============================================================

-- Extensiones
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- ============================================================
-- LEADS
-- ============================================================
CREATE TABLE IF NOT EXISTS leads (
    id              SERIAL PRIMARY KEY,
    phone           VARCHAR(20)  UNIQUE NOT NULL,
    name            VARCHAR(100),
    source          VARCHAR(50)  NOT NULL DEFAULT 'whatsapp',
    status          VARCHAR(20)  NOT NULL DEFAULT 'new',
    interest        VARCHAR(100),
    budget_range    VARCHAR(50),
    qualification   JSONB        NOT NULL DEFAULT '{}',
    human_takeover  BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- status válidos: new, contacted, qualified, appointment_set, closed, lost
CREATE INDEX IF NOT EXISTS idx_leads_phone  ON leads (phone);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads (status);
CREATE INDEX IF NOT EXISTS idx_leads_created_at ON leads (created_at DESC);

-- Trigger para auto-actualizar updated_at
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER leads_updated_at
    BEFORE UPDATE ON leads
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ============================================================
-- CONVERSATIONS
-- ============================================================
CREATE TABLE IF NOT EXISTS conversations (
    id          SERIAL PRIMARY KEY,
    lead_id     INT         NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    role        VARCHAR(10) NOT NULL,   -- 'user' | 'assistant' | 'tool'
    content     TEXT        NOT NULL,
    metadata    JSONB       NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- role CHECK
ALTER TABLE conversations
    DROP CONSTRAINT IF EXISTS conversations_role_check;
ALTER TABLE conversations
    ADD CONSTRAINT conversations_role_check
    CHECK (role IN ('user', 'assistant', 'tool'));

CREATE INDEX IF NOT EXISTS idx_conversations_lead_created
    ON conversations (lead_id, created_at DESC);

-- ============================================================
-- APPOINTMENTS
-- ============================================================
CREATE TABLE IF NOT EXISTS appointments (
    id               SERIAL PRIMARY KEY,
    lead_id          INT         NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    scheduled_date   DATE        NOT NULL,
    scheduled_time   TIME        NOT NULL,
    status           VARCHAR(20) NOT NULL DEFAULT 'scheduled',
    product_interest VARCHAR(200),
    reminder_sent    BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- status válidos: scheduled, confirmed, cancelled, completed, no_show
ALTER TABLE appointments
    DROP CONSTRAINT IF EXISTS appointments_status_check;
ALTER TABLE appointments
    ADD CONSTRAINT appointments_status_check
    CHECK (status IN ('scheduled', 'confirmed', 'cancelled', 'completed', 'no_show'));

CREATE INDEX IF NOT EXISTS idx_appointments_date_status
    ON appointments (scheduled_date, status);
CREATE INDEX IF NOT EXISTS idx_appointments_lead_id
    ON appointments (lead_id);

-- ============================================================
-- FOLLOW_UPS
-- ============================================================
CREATE TABLE IF NOT EXISTS follow_ups (
    id             SERIAL PRIMARY KEY,
    lead_id        INT         NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    attempt_number INT         NOT NULL DEFAULT 1,
    sent_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    responded      BOOLEAN     NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_followups_lead_attempt
    ON follow_ups (lead_id, attempt_number);
CREATE INDEX IF NOT EXISTS idx_followups_sent_at
    ON follow_ups (sent_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_followups_lead_attempt_unique
    ON follow_ups (lead_id, attempt_number);
