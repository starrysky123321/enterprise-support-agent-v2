ALTER TABLE knowledge_backend.document_versions
    ADD COLUMN available_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN cycle_attempt integer NOT NULL DEFAULT 0;

DROP INDEX knowledge_backend.document_versions_pending_jobs;

CREATE INDEX document_versions_pending_jobs
    ON knowledge_backend.document_versions (available_at, created_at, id)
    WHERE status = 'PENDING';

CREATE INDEX document_versions_running_leases
    ON knowledge_backend.document_versions (lease_until, id)
    WHERE status = 'INDEXING';
