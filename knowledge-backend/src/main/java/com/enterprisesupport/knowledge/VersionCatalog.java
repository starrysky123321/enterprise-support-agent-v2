package com.enterprisesupport.knowledge;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;

@Repository
class VersionCatalog {
    private static final String STATUS_QUERY = """
            SELECT v.document_id, v.version_number, v.status, v.attempt, v.failure_reason,
                   d.published_version
            FROM knowledge_backend.document_versions v
            JOIN knowledge_backend.documents d
                ON d.workspace_id = v.workspace_id AND d.id = v.document_id
            WHERE v.workspace_id = :workspaceId AND v.document_id = :documentId AND v.retired_at IS NULL
            """;
    private static final String JOB_COLUMNS = "id, workspace_id, document_id, version_number, body, filename, pdf, pdf_storage_key, document_type, product_name, product_version, attempt, cycle_attempt";

    private final JdbcClient db;
    private final IndexOutbox outbox;
    private final PdfBlobStore blobStore;
    private final boolean kafkaEnabled;

    VersionCatalog(JdbcClient db, IndexOutbox outbox, PdfBlobStore blobStore, KnowledgeProperties config) {
        this.db = db;
        this.outbox = outbox;
        this.blobStore = blobStore;
        this.kafkaEnabled = config.kafkaEnabled();
    }

    record VersionStatus(String documentId, long versionNumber, String status, int attempt,
                         String failureReason, Long publishedVersion) {
    }

    record PublishedVersion(UUID id, long versionNumber) {
    }

    record PublishedDocument(UUID indexId, String documentId, long version, String filename) {
    }

    record PublishedWorkspace(long catalogVersion, List<PublishedDocument> documents) {
    }

    record Metadata(String documentType, String productName, String productVersion) {
        static final Metadata EMPTY = new Metadata(null, null, null);
    }

    record DocumentSummary(String documentId, long latestVersion, Long publishedVersion,
                           String status, String contentType, String filename,
                           String documentType, String productName, String productVersion) {
    }

    record IndexJob(UUID id, String workspaceId, String documentId, long versionNumber,
                    String body, String filename, byte[] pdf, String pdfStorageKey,
                    String documentType, String productName, String productVersion,
                    int attempt, int cycleAttempt) {
    }

    record PurgeJob(UUID id, int purgeAttempt, String pdfStorageKey) {
    }

    private record PublishedWorkspaceRow(long catalogVersion, UUID indexId, String documentId,
                                         Long version, String filename) {
    }

    @Transactional
    public VersionStatus submit(String workspaceId, String documentId, String body, Metadata metadata) {
        return insertVersion(workspaceId, documentId, TokenDigest.sha256(body), "text/plain",
                body, null, null, metadata == null ? Metadata.EMPTY : metadata);
    }

    @Transactional
    public VersionStatus submitPdf(String workspaceId, String documentId, String filename,
                                   PdfBlobStore.StagedPdf staged, Metadata metadata) {
        return insertVersion(workspaceId, documentId, staged.sha256(), "application/pdf",
                null, filename, staged, metadata == null ? Metadata.EMPTY : metadata);
    }

    private VersionStatus insertVersion(String workspaceId, String documentId, String hash, String contentType,
                                        String body, String filename, PdfBlobStore.StagedPdf stagedPdf,
                                        Metadata metadata) {
        db.sql("""
                INSERT INTO knowledge_backend.documents (workspace_id, id)
                VALUES (:workspaceId, :id) ON CONFLICT DO NOTHING
                """).param("workspaceId", workspaceId).param("id", documentId).update();
        long current = db.sql("""
                SELECT last_version FROM knowledge_backend.documents
                WHERE workspace_id = :workspaceId AND id = :id FOR UPDATE
                """).param("workspaceId", workspaceId).param("id", documentId).query(Long.class).single();

        var existing = db.sql(STATUS_QUERY + " AND v.content_hash = :hash AND v.content_type = :type")
                .param("workspaceId", workspaceId).param("documentId", documentId).param("hash", hash)
                .param("type", contentType)
                .query(VersionStatus.class).optional();
        if (existing.isPresent()) {
            return existing.get();
        }

        long version = current + 1;
        UUID jobId = UUID.randomUUID();
        byte[] pdfBytes = null;
        String pdfStorageKey = null;
        if (stagedPdf != null) {
            if (blobStore.usesExternalStorage()) {
                try {
                    pdfStorageKey = blobStore.commit(jobId, stagedPdf);
                } catch (IOException error) {
                    throw new UncheckedIOException("Failed to persist PDF blob", error);
                }
            } else {
                pdfBytes = stagedPdf.bytes();
            }
        }
        try {
            db.sql("""
                    INSERT INTO knowledge_backend.document_versions
                        (id, workspace_id, document_id, version_number, body, content_hash, status,
                         content_type, filename, pdf, pdf_storage_key,
                         document_type, product_name, product_version)
                    VALUES (:id, :workspaceId, :documentId, :version, :body, :hash, 'PENDING',
                         :type, :name, :pdf, :storageKey,
                         :documentType, :productName, :productVersion)
                    """)
                    .param("id", jobId).param("workspaceId", workspaceId).param("documentId", documentId)
                    .param("version", version).param("body", body).param("hash", hash).param("type", contentType)
                    .param("name", filename).param("pdf", pdfBytes, java.sql.Types.BINARY)
                    .param("storageKey", pdfStorageKey)
                    .param("documentType", metadata.documentType())
                    .param("productName", metadata.productName())
                    .param("productVersion", metadata.productVersion()).update();
            db.sql("""
                    UPDATE knowledge_backend.documents SET last_version = :version, deleted_at = NULL
                    WHERE workspace_id = :workspaceId AND id = :id
                    """).param("workspaceId", workspaceId).param("id", documentId).param("version", version).update();
            if (kafkaEnabled) {
                outbox.add(jobId);
            }
            return status(workspaceId, documentId, version).orElseThrow();
        } catch (RuntimeException failure) {
            if (pdfStorageKey != null) {
                blobStore.deleteQuietly(pdfStorageKey);
            }
            throw failure;
        }
    }

    Optional<VersionStatus> status(String workspaceId, String documentId, long version) {
        return db.sql(STATUS_QUERY + " AND v.version_number = :version")
                .param("workspaceId", workspaceId).param("documentId", documentId).param("version", version)
                .query(VersionStatus.class).optional();
    }

    @Transactional
    public Optional<VersionStatus> retry(String workspaceId, String documentId, long version) {
        var resumed = db.sql("""
                UPDATE knowledge_backend.document_versions
                SET status = 'PENDING', failure_reason = NULL, available_at = now(), cycle_attempt = 0
                WHERE workspace_id = :workspaceId AND document_id = :documentId
                    AND version_number = :version AND status = 'FAILED' AND retired_at IS NULL
                RETURNING id
                """)
                .param("workspaceId", workspaceId).param("documentId", documentId).param("version", version)
                .query(UUID.class).optional();
        if (kafkaEnabled) {
            resumed.ifPresent(outbox::add);
        }
        return status(workspaceId, documentId, version);
    }

    Optional<PublishedVersion> published(String workspaceId, String documentId) {
        return db.sql("""
                SELECT v.id, v.version_number
                FROM knowledge_backend.documents d
                JOIN knowledge_backend.document_versions v
                    ON v.workspace_id = d.workspace_id AND v.document_id = d.id
                    AND v.version_number = d.published_version
                WHERE d.workspace_id = :workspaceId AND d.id = :documentId AND d.deleted_at IS NULL
                """)
                .param("workspaceId", workspaceId).param("documentId", documentId)
                .query(PublishedVersion.class).optional();
    }

    PublishedWorkspace publishedWorkspace(String workspaceId) {
        var rows = db.sql("""
                SELECT w.catalog_version, v.id AS index_id, d.id AS document_id,
                       v.version_number AS version, v.filename
                FROM knowledge_backend.workspaces w
                LEFT JOIN knowledge_backend.documents d
                    ON d.workspace_id = w.id AND d.deleted_at IS NULL
                LEFT JOIN knowledge_backend.document_versions v
                    ON v.workspace_id = d.workspace_id AND v.document_id = d.id
                    AND v.version_number = d.published_version
                WHERE w.id = :workspaceId
                ORDER BY d.id
                """).param("workspaceId", workspaceId).query(PublishedWorkspaceRow.class).list();
        long catalogVersion = rows.isEmpty() ? 0L : rows.get(0).catalogVersion();
        var documents = rows.stream()
                .filter(row -> row.indexId() != null)
                .map(row -> new PublishedDocument(row.indexId(), row.documentId(), row.version(), row.filename()))
                .toList();
        return new PublishedWorkspace(catalogVersion, documents);
    }

    List<DocumentSummary> listDocuments(String workspaceId, String after, int limit) {
        return db.sql("""
                SELECT d.id AS document_id, d.last_version AS latest_version, d.published_version,
                       v.status, v.content_type, v.filename,
                       v.document_type, v.product_name, v.product_version
                FROM knowledge_backend.documents d
                JOIN knowledge_backend.document_versions v ON v.workspace_id = d.workspace_id
                    AND v.document_id = d.id AND v.version_number = d.last_version
                WHERE d.workspace_id = :workspaceId AND d.deleted_at IS NULL AND d.id > :after
                ORDER BY d.id LIMIT :limit
                """).param("workspaceId", workspaceId).param("after", after).param("limit", limit)
                .query(DocumentSummary.class).list();
    }

    @Transactional
    public void deleteDocument(String workspaceId, String documentId) {
        int updated = db.sql("""
                UPDATE knowledge_backend.documents SET deleted_at = now(), published_version = NULL
                WHERE workspace_id = :workspaceId AND id = :documentId AND deleted_at IS NULL
                """).param("workspaceId", workspaceId).param("documentId", documentId).update();
        if (updated == 1) {
            db.sql("""
                    UPDATE knowledge_backend.document_versions SET retired_at = now(), purge_available_at = now()
                    WHERE workspace_id = :workspaceId AND document_id = :documentId AND retired_at IS NULL
                    """).param("workspaceId", workspaceId).param("documentId", documentId).update();
            bumpCatalogVersion(workspaceId);
        }
    }

    Optional<PurgeJob> claimPurge() {
        return db.sql("""
                WITH candidate AS (
                    SELECT id FROM knowledge_backend.document_versions
                    WHERE retired_at IS NOT NULL AND purged_at IS NULL AND purge_available_at <= now()
                    ORDER BY purge_available_at, id LIMIT 1 FOR UPDATE SKIP LOCKED
                )
                UPDATE knowledge_backend.document_versions v
                SET purge_available_at = now() + interval '2 minutes', purge_attempt = v.purge_attempt + 1
                FROM candidate WHERE v.id = candidate.id RETURNING v.id, v.purge_attempt, v.pdf_storage_key
                """).query(PurgeJob.class).optional();
    }

    void completePurge(PurgeJob job) {
        db.sql("""
                UPDATE knowledge_backend.document_versions
                SET body = NULL, pdf = NULL, pdf_storage_key = NULL, purged_at = now()
                WHERE id = :id AND purge_attempt = :attempt AND retired_at IS NOT NULL AND purged_at IS NULL
                """).param("id", job.id()).param("attempt", job.purgeAttempt()).update();
    }

    Optional<IndexJob> claimNext(int maxAttempts) {
        return db.sql("""
                WITH next_job AS (
                    SELECT id FROM knowledge_backend.document_versions
                    WHERE retired_at IS NULL AND ((status = 'PENDING' AND available_at <= now())
                        OR (status = 'INDEXING' AND lease_until <= now() AND cycle_attempt < :maxAttempts))
                    ORDER BY available_at, created_at, id
                    LIMIT 1 FOR UPDATE SKIP LOCKED
                )
                UPDATE knowledge_backend.document_versions v
                SET status = 'INDEXING', attempt = v.attempt + 1, cycle_attempt = v.cycle_attempt + 1,
                    lease_until = now() + interval '2 minutes', failure_reason = NULL
                FROM next_job
                WHERE v.id = next_job.id
                RETURNING v.id, v.workspace_id, v.document_id, v.version_number, v.body, v.filename, v.pdf,
                    v.pdf_storage_key, v.document_type, v.product_name, v.product_version,
                    v.attempt, v.cycle_attempt
                """).param("maxAttempts", maxAttempts).query(IndexJob.class).optional();
    }

    Optional<IndexJob> claimDispatched(UUID id) {
        return db.sql("""
                UPDATE knowledge_backend.document_versions
                SET status = 'INDEXING', attempt = attempt + 1, cycle_attempt = cycle_attempt + 1,
                    lease_until = now() + interval '2 minutes', failure_reason = NULL
                WHERE id = :id AND retired_at IS NULL AND status = 'PENDING' AND available_at <= now()
                RETURNING
                """ + JOB_COLUMNS).param("id", id).query(IndexJob.class).optional();
    }

    @Transactional
    public void reconcileDispatch(int maxAttempts) {
        db.sql("""
                WITH stranded AS (
                    SELECT v.id FROM knowledge_backend.document_versions v
                    WHERE v.retired_at IS NULL AND ((v.status = 'INDEXING' AND v.lease_until <= now() AND v.cycle_attempt < :maxAttempts)
                       OR (v.status = 'PENDING' AND v.available_at <= now()
                           AND (v.last_enqueued_at IS NULL OR v.last_enqueued_at <= now() - interval '15 minutes')
                           AND NOT EXISTS (SELECT 1 FROM knowledge_backend.index_outbox o WHERE o.job_id = v.id)))
                    ORDER BY available_at, created_at LIMIT 64 FOR UPDATE OF v SKIP LOCKED
                )
                UPDATE knowledge_backend.document_versions v
                SET status = 'PENDING', lease_until = NULL, available_at = now()
                FROM stranded WHERE v.id = stranded.id
                RETURNING v.id
                """).param("maxAttempts", maxAttempts).query(UUID.class).list().forEach(outbox::add);
    }

    int failExpired(int maxAttempts) {
        return db.sql("""
                WITH expired AS (
                    SELECT id FROM knowledge_backend.document_versions
                    WHERE retired_at IS NULL AND status = 'INDEXING' AND lease_until <= now() AND cycle_attempt >= :maxAttempts
                    ORDER BY lease_until LIMIT 64 FOR UPDATE SKIP LOCKED
                )
                UPDATE knowledge_backend.document_versions v
                SET status = 'FAILED', lease_until = NULL, failure_reason = 'Index worker lease expired'
                FROM expired WHERE v.id = expired.id
                """).param("maxAttempts", maxAttempts).update();
    }

    @Transactional
    public boolean publish(IndexJob job) {
        // Acquiring the document before its versions matches the order used by deletion.
        db.sql("""
                SELECT id FROM knowledge_backend.documents
                WHERE workspace_id = :workspaceId AND id = :documentId FOR UPDATE
                """).param("workspaceId", job.workspaceId()).param("documentId", job.documentId())
                .query(String.class).single();
        int updated = db.sql("""
                UPDATE knowledge_backend.document_versions
                SET status = 'INDEXED', lease_until = NULL
                WHERE id = :id AND attempt = :attempt AND status = 'INDEXING' AND retired_at IS NULL
                """)
                .param("id", job.id()).param("attempt", job.attempt()).update();
        if (updated == 0) {
            return false;
        }
        int published = db.sql("""
                UPDATE knowledge_backend.documents SET published_version = :version
                WHERE workspace_id = :workspaceId AND id = :documentId
                    AND (published_version IS NULL OR published_version < :version)
                """)
                .param("workspaceId", job.workspaceId()).param("documentId", job.documentId())
                .param("version", job.versionNumber()).update();
        if (published > 0) {
            bumpCatalogVersion(job.workspaceId());
        }
        return true;
    }

    private void bumpCatalogVersion(String workspaceId) {
        db.sql("""
                UPDATE knowledge_backend.workspaces SET catalog_version = catalog_version + 1
                WHERE id = :workspaceId
                """).param("workspaceId", workspaceId).update();
    }

    @Transactional
    public boolean fail(IndexJob job, String reason, int retryAfterSeconds) {
        boolean updated = db.sql("""
                UPDATE knowledge_backend.document_versions
                SET status = :status, lease_until = NULL, failure_reason = :reason,
                    available_at = now() + make_interval(secs => :delay)
                WHERE id = :id AND attempt = :attempt AND status = 'INDEXING' AND retired_at IS NULL
                """)
                .param("status", retryAfterSeconds > 0 ? "PENDING" : "FAILED").param("delay", retryAfterSeconds)
                .param("id", job.id()).param("attempt", job.attempt()).param("reason", reason).update() > 0;
        if (updated && retryAfterSeconds > 0 && kafkaEnabled) {
            outbox.add(job.id());
        }
        return updated;
    }
}
