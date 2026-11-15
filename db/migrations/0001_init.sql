-- 0001_init: mock data systems (DC-3, DC-8) and call records.
-- Plain Postgres 15+ SQL. No Supabase-specific features. Table names are
-- unqualified so the runner's search_path picks the schema (tests use a
-- throwaway schema). Mock data only: no real patient data, ever.

CREATE TABLE plans (
    plan_id               text PRIMARY KEY,
    name                  text NOT NULL,
    plan_type             text NOT NULL CHECK (plan_type IN ('HMO', 'PPO', 'EPO', 'HDHP', 'Medicare Advantage', 'Medicaid')),
    requires_referral     boolean NOT NULL DEFAULT false,
    deductible            numeric(10, 2) NOT NULL,
    out_of_pocket_max     numeric(10, 2) NOT NULL,
    pcp_copay             numeric(10, 2) NOT NULL,
    specialist_copay      numeric(10, 2) NOT NULL,
    monthly_premium       numeric(10, 2) NOT NULL
);

CREATE TABLE members (
    member_id             text PRIMARY KEY,
    first_name            text NOT NULL,
    last_name             text NOT NULL,
    dob                   date NOT NULL,
    phone                 text NOT NULL,
    plan_id               text NOT NULL REFERENCES plans (plan_id),
    coverage_start        date NOT NULL,
    coverage_end          date
);
CREATE INDEX members_identity_idx ON members (lower(last_name), lower(first_name), dob);
CREATE INDEX members_phone_idx ON members (phone);

CREATE TABLE prior_authorizations (
    auth_id               text PRIMARY KEY,
    member_id             text NOT NULL REFERENCES members (member_id),
    service_description   text NOT NULL,
    provider_name         text NOT NULL,
    status                text NOT NULL CHECK (status IN ('pending', 'approved', 'denied', 'expired')),
    requested_on          date NOT NULL,
    decided_on            date,
    valid_from            date,
    valid_to              date,
    notes                 text
);
CREATE INDEX prior_authorizations_member_idx ON prior_authorizations (member_id);

CREATE TABLE claims (
    claim_id              text PRIMARY KEY,
    member_id             text NOT NULL REFERENCES members (member_id),
    service_date          date NOT NULL,
    provider_name         text NOT NULL,
    service_description   text NOT NULL,
    billed_amount         numeric(10, 2) NOT NULL,
    allowed_amount        numeric(10, 2),
    member_responsibility numeric(10, 2),
    status                text NOT NULL CHECK (status IN ('received', 'in_review', 'pending_info', 'approved', 'paid', 'denied')),
    denial_code           text,
    denial_reason         text,
    received_on           date NOT NULL,
    processed_on          date,
    auth_id               text REFERENCES prior_authorizations (auth_id),
    CONSTRAINT claims_denial_code_iff_denied CHECK ((status = 'denied') = (denial_code IS NOT NULL))
);
CREATE INDEX claims_member_idx ON claims (member_id, service_date DESC);

CREATE TABLE payments (
    payment_id            text PRIMARY KEY,
    member_id             text NOT NULL REFERENCES members (member_id),
    paid_at               timestamptz NOT NULL,
    amount                numeric(10, 2) NOT NULL,
    kind                  text NOT NULL CHECK (kind IN ('premium', 'copay', 'claim_balance', 'refund')),
    method                text NOT NULL CHECK (method IN ('card', 'bank', 'check', 'autopay')),
    status                text NOT NULL CHECK (status IN ('posted', 'pending', 'reversed')),
    reference             text NOT NULL
);
CREATE INDEX payments_member_idx ON payments (member_id, paid_at DESC);

CREATE TABLE referrals (
    referral_id           text PRIMARY KEY,
    member_id             text NOT NULL REFERENCES members (member_id),
    from_provider         text NOT NULL,
    to_provider           text NOT NULL,
    specialty             text NOT NULL,
    status                text NOT NULL CHECK (status IN ('pending', 'approved', 'denied', 'expired')),
    created_on            date NOT NULL,
    valid_to              date,
    visits_allowed        integer
);
CREATE INDEX referrals_member_idx ON referrals (member_id);

-- Past calls, for caller history and repeat caller detection (DC-8, AP-13).
CREATE TABLE interactions (
    interaction_id        text PRIMARY KEY,
    member_id             text NOT NULL REFERENCES members (member_id),
    started_at            timestamptz NOT NULL,
    duration_seconds      integer NOT NULL CHECK (duration_seconds >= 0),
    queue                 text NOT NULL,
    outcome               text NOT NULL,
    notes                 text NOT NULL DEFAULT ''
);
CREATE INDEX interactions_member_idx ON interactions (member_id, started_at DESC);

CREATE TABLE kb_articles (
    article_id            text PRIMARY KEY,
    title                 text NOT NULL,
    category              text NOT NULL CHECK (category IN ('billing', 'claims')),
    tags                  text[] NOT NULL DEFAULT '{}',
    body                  text NOT NULL,
    updated_on            date NOT NULL
);

-- Live call records written by the bot and server.
CREATE TABLE calls (
    call_id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id            text NOT NULL UNIQUE,
    status                text NOT NULL DEFAULT 'intake'
                          CHECK (status IN ('intake', 'ready_for_queue', 'queued', 'with_agent', 'ended', 'abandoned')),
    member_id             text REFERENCES members (member_id),
    intake                jsonb NOT NULL DEFAULT '{}'::jsonb,
    identity_result       text CHECK (identity_result IN ('verified', 'not_verified', 'not_found')),
    flags                 text[] NOT NULL DEFAULT '{}',
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    queued_at             timestamptz,
    answered_at           timestamptz,
    ended_at              timestamptz
);
CREATE INDEX calls_status_idx ON calls (status, created_at);

CREATE TABLE transcript_lines (
    line_id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    call_id               uuid NOT NULL REFERENCES calls (call_id) ON DELETE CASCADE,
    speaker_role          text NOT NULL CHECK (speaker_role IN ('caller', 'bot', 'agent')),
    text                  text NOT NULL,
    ts                    timestamptz NOT NULL DEFAULT now(),
    tone                  text
);
CREATE INDEX transcript_lines_call_idx ON transcript_lines (call_id, ts);
