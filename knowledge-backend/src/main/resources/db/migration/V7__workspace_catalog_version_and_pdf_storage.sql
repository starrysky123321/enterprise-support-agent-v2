ALTER TABLE knowledge_backend.workspaces
    ADD COLUMN catalog_version bigint NOT NULL DEFAULT 0;

ALTER TABLE knowledge_backend.document_versions
    ADD COLUMN pdf_storage_key varchar(255);

ALTER TABLE knowledge_backend.document_versions
    DROP CONSTRAINT document_versions_payload,
    ADD CONSTRAINT document_versions_payload CHECK (
        (purged_at IS NOT NULL AND retired_at IS NOT NULL
            AND body IS NULL AND pdf IS NULL AND pdf_storage_key IS NULL)
        OR (purged_at IS NULL AND (
            (content_type = 'text/plain' AND body IS NOT NULL AND pdf IS NULL AND pdf_storage_key IS NULL AND filename IS NULL)
            OR (content_type = 'application/pdf' AND body IS NULL AND filename IS NOT NULL
                AND ((pdf IS NOT NULL) <> (pdf_storage_key IS NOT NULL)))
        ))
    );
