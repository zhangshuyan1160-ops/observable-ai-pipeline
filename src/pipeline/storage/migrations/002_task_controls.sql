ALTER TABLE tasks DROP CONSTRAINT tasks_status_check;
ALTER TABLE tasks ADD CONSTRAINT tasks_status_check CHECK
    (status IN ('QUEUED','RUNNING','CANCELLING','CANCELLED','SUCCEEDED','FAILED'));
ALTER TABLE steps DROP CONSTRAINT steps_status_check;
ALTER TABLE steps ADD CONSTRAINT steps_status_check CHECK
    (status IN ('PENDING','READY','WAITING_RESOURCE','RUNNING','RETRY_WAIT',
               'SUCCEEDED','CACHED','FAILED','BLOCKED','CANCELLED'));
ALTER TABLE attempts DROP CONSTRAINT attempts_status_check;
ALTER TABLE attempts ADD CONSTRAINT attempts_status_check CHECK
    (status IN ('RUNNING','SUCCEEDED','FAILED','TIMED_OUT','INTERRUPTED','CANCELLED'));
ALTER TABLE tasks ADD COLUMN cancel_requested_at timestamptz;
ALTER TABLE tasks ADD COLUMN cancelled_at timestamptz;
ALTER TABLE tasks ADD COLUMN cancel_unconfirmed boolean NOT NULL DEFAULT false;
ALTER TABLE tasks ADD COLUMN archived_at timestamptz;
CREATE INDEX tasks_history_idx ON tasks(created_at DESC,id DESC);
CREATE INDEX steps_resource_ready_idx ON steps(resource,next_run_at,task_id)
    WHERE status IN ('READY','WAITING_RESOURCE','RETRY_WAIT');
INSERT INTO schema_version VALUES (2);
