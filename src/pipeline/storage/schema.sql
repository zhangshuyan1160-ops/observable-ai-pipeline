CREATE TABLE IF NOT EXISTS schema_version (version integer PRIMARY KEY);
INSERT INTO schema_version VALUES (1) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS tasks (
    id text PRIMARY KEY,
    idempotency_key text NOT NULL UNIQUE,
    request_hash text NOT NULL,
    params jsonb NOT NULL,
    definition jsonb NOT NULL,
    status text NOT NULL CHECK(status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')),
    event_seq bigint NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    deadline timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS steps (
    id text PRIMARY KEY,
    task_id text NOT NULL REFERENCES tasks(id),
    key text NOT NULL,
    ordinal integer NOT NULL,
    label text NOT NULL,
    depends_on jsonb NOT NULL,
    resource text NOT NULL CHECK(resource IN ('cpu','gpu')),
    status text NOT NULL CHECK(status IN ('PENDING','READY','WAITING_RESOURCE','RUNNING',
        'RETRY_WAIT','SUCCEEDED','CACHED','FAILED','BLOCKED')),
    current_attempt_id text,
    token bigint NOT NULL DEFAULT 0,
    retry_round integer NOT NULL DEFAULT 0,
    attempts_in_round integer NOT NULL DEFAULT 0,
    fingerprint text,
    checkpoint jsonb NOT NULL DEFAULT '{}'::jsonb,
    progress real NOT NULL DEFAULT 0 CHECK(progress BETWEEN 0 AND 1),
    artifact_id text,
    error text,
    wait_since timestamptz,
    next_run_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(task_id,key)
);
CREATE INDEX IF NOT EXISTS steps_task_idx ON steps(task_id,ordinal);
CREATE INDEX IF NOT EXISTS steps_queue_idx ON steps(status,next_run_at);
CREATE TABLE IF NOT EXISTS attempts (
    id text PRIMARY KEY,
    task_id text NOT NULL REFERENCES tasks(id),
    step_id text NOT NULL REFERENCES steps(id),
    worker_id text NOT NULL,
    token bigint NOT NULL,
    retry_round integer NOT NULL,
    attempt_no integer NOT NULL,
    status text NOT NULL CHECK(status IN ('RUNNING','SUCCEEDED','FAILED','TIMED_OUT','INTERRUPTED')),
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    heartbeat_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    lease_expires_at timestamptz NOT NULL,
    deadline timestamptz NOT NULL,
    resume_from integer NOT NULL DEFAULT 0,
    error_type text,
    error text,
    UNIQUE(step_id,retry_round,attempt_no)
);
CREATE INDEX IF NOT EXISTS attempts_step_idx ON attempts(step_id,started_at);
CREATE INDEX IF NOT EXISTS attempts_active_idx ON attempts(lease_expires_at) WHERE status='RUNNING';
CREATE TABLE IF NOT EXISTS events (
    task_id text NOT NULL REFERENCES tasks(id),
    seq bigint NOT NULL,
    type text NOT NULL,
    step_key text,
    attempt_id text,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(task_id,seq)
);
CREATE TABLE IF NOT EXISTS artifacts (
    id text PRIMARY KEY,
    path text NOT NULL,
    sha256 text NOT NULL,
    size bigint NOT NULL CHECK(size>=0),
    media_type text NOT NULL,
    name text NOT NULL,
    attempt_id text NOT NULL REFERENCES attempts(id),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS cache_entries (
    fingerprint text PRIMARY KEY,
    artifact_id text NOT NULL REFERENCES artifacts(id),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS workers (
    id text PRIMARY KEY,
    resource text NOT NULL,
    pid integer NOT NULL,
    heartbeat_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS resource_slots (
    id text PRIMARY KEY,
    capacity_mb integer NOT NULL,
    attempt_id text UNIQUE REFERENCES attempts(id),
    token bigint,
    lease_expires_at timestamptz
);
INSERT INTO resource_slots(id,capacity_mb) VALUES ('gpu-mock-0',8192) ON CONFLICT DO NOTHING;
