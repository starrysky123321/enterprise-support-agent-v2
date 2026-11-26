ALTER TABLE knowledge_backend.documents ADD COLUMN deleted_at timestamptz;

ALTER TABLE knowledge_backend.document_versions
    ALTER COLUMN body DROP NOT NULL,
    ADD COLUMN content_type text NOT NULL DEFAULT 'text/plain',
    ADD COLUMN filename varchar(255),
    ADD COLUMN pdf bytea,
    ADD COLUMN retired_at timestamptz,
    ADD COLUMN purged_at timestamptz,
    ADD COLUMN purge_available_at timestamptz,
    ADD COLUMN purge_attempt integer NOT NULL DEFAULT 0;

ALTER TABLE knowledge_backend.document_versions
    DROP CONSTRAINT document_versions_workspace_id_document_id_content_hash_key,
    ADD CONSTRAINT document_versions_payload CHECK (
        (purged_at IS NOT NULL AND retired_at IS NOT NULL AND body IS NULL AND pdf IS NULL)
        OR (purged_at IS NULL AND (
            (content_type = 'text/plain' AND body IS NOT NULL AND pdf IS NULL AND filename IS NULL)
            OR (content_type = 'application/pdf' AND body IS NULL AND pdf IS NOT NULL AND filename IS NOT NULL)
        ))
    );

CREATE UNIQUE INDEX document_versions_active_content
    ON knowledge_backend.document_versions (workspace_id, document_id, content_type, content_hash)
    WHERE retired_at IS NULL;

CREATE INDEX document_versions_pending_purge
    ON knowledge_backend.document_versions (purge_available_at, id)
    WHERE retired_at IS NOT NULL AND purged_at IS NULL;
