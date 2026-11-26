ALTER TABLE knowledge_backend.document_versions
    ADD COLUMN document_type varchar(32),
    ADD COLUMN product_name varchar(128),
    ADD COLUMN product_version varchar(64);
