CREATE TABLE knowledge_backend.documents (
    id varchar(64) PRIMARY KEY,
    last_version bigint NOT NULL DEFAULT 0,
    published_version bigint,
    CHECK (published_version > 0 AND published_version <= last_version)
);

CREATE TABLE knowledge_backend.document_versions (
    id uuid PRIMARY KEY,
    document_id varchar(64) NOT NULL REFERENCES knowledge_backend.documents(id),
    version_number bigint NOT NULL CHECK (version_number > 0),
    body text NOT NULL,
    content_hash char(64) NOT NULL,
    status varchar(16) NOT NULL CHECK (status IN ('PENDING', 'INDEXING', 'FAILED', 'INDEXED')),
    attempt integer NOT NULL DEFAULT 0,
    lease_until timestamptz,
    failure_reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, version_number),
    UNIQUE (document_id, content_hash),
    CHECK ((status = 'INDEXING') = (lease_until IS NOT NULL))
);

ALTER TABLE knowledge_backend.documents
    ADD FOREIGN KEY (id, published_version)
    REFERENCES knowledge_backend.document_versions (document_id, version_number);

CREATE INDEX document_versions_pending_jobs
    ON knowledge_backend.document_versions (created_at, id)
    WHERE status IN ('PENDING', 'INDEXING');
