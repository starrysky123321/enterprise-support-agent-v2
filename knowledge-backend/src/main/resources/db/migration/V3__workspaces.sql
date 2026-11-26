CREATE TABLE knowledge_backend.workspaces (
    id varchar(64) PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO knowledge_backend.workspaces (id) VALUES ('default');

ALTER TABLE knowledge_backend.documents DROP CONSTRAINT documents_id_published_version_fkey;
ALTER TABLE knowledge_backend.document_versions DROP CONSTRAINT document_versions_document_id_fkey;
ALTER TABLE knowledge_backend.document_versions
    DROP CONSTRAINT document_versions_document_id_version_number_key,
    DROP CONSTRAINT document_versions_document_id_content_hash_key;
ALTER TABLE knowledge_backend.documents DROP CONSTRAINT documents_pkey;

ALTER TABLE knowledge_backend.documents
    ADD COLUMN workspace_id varchar(64) NOT NULL DEFAULT 'default' REFERENCES knowledge_backend.workspaces(id),
    ADD PRIMARY KEY (workspace_id, id);
ALTER TABLE knowledge_backend.document_versions
    ADD COLUMN workspace_id varchar(64) NOT NULL DEFAULT 'default',
    ADD FOREIGN KEY (workspace_id, document_id) REFERENCES knowledge_backend.documents (workspace_id, id),
    ADD UNIQUE (workspace_id, document_id, version_number),
    ADD UNIQUE (workspace_id, document_id, content_hash);
ALTER TABLE knowledge_backend.documents
    ADD FOREIGN KEY (workspace_id, id, published_version)
    REFERENCES knowledge_backend.document_versions (workspace_id, document_id, version_number);

CREATE TABLE knowledge_backend.workspace_keys (
    id uuid PRIMARY KEY,
    workspace_id varchar(64) NOT NULL REFERENCES knowledge_backend.workspaces(id),
    token_hash char(64) NOT NULL UNIQUE,
    role varchar(8) NOT NULL CHECK (role IN ('READER', 'EDITOR')),
    created_at timestamptz NOT NULL DEFAULT now()
);
