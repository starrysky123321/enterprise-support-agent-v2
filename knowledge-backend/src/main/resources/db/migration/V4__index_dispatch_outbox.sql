ALTER TABLE knowledge_backend.document_versions ADD COLUMN last_enqueued_at timestamptz;

CREATE TABLE knowledge_backend.index_outbox (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id uuid NOT NULL REFERENCES knowledge_backend.document_versions(id) ON DELETE CASCADE,
    available_at timestamptz NOT NULL,
    lease_until timestamptz,
    dispatch_attempt integer NOT NULL DEFAULT 0
);

CREATE INDEX index_outbox_schedule ON knowledge_backend.index_outbox (available_at, id);
CREATE INDEX index_outbox_job ON knowledge_backend.index_outbox (job_id);
