package com.enterprisesupport.knowledge.repository;

import java.util.List;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

@Repository
public class IndexOutbox {
    private final JdbcClient db;

    public IndexOutbox(JdbcClient db) {
        this.db = db;
    }

    public record Message(long id, UUID jobId, int dispatchAttempt) {
    }

    public void add(UUID jobId) {
        db.sql("""
                WITH scheduled AS (
                    UPDATE knowledge_backend.document_versions SET last_enqueued_at = now()
                    WHERE id = :jobId RETURNING id, available_at
                )
                INSERT INTO knowledge_backend.index_outbox (job_id, available_at)
                SELECT id, available_at FROM scheduled
                """).param("jobId", jobId).update();
    }

    public List<Message> claimBatch() {
        return db.sql("""
                WITH ready AS (
                    SELECT id FROM knowledge_backend.index_outbox
                    WHERE available_at <= now() AND (lease_until IS NULL OR lease_until <= now())
                    ORDER BY available_at, id LIMIT 32 FOR UPDATE SKIP LOCKED
                )
                UPDATE knowledge_backend.index_outbox events
                SET lease_until = now() + interval '2 minutes', dispatch_attempt = events.dispatch_attempt + 1
                FROM ready WHERE events.id = ready.id
                RETURNING events.id, events.job_id, events.dispatch_attempt
                """).query(Message.class).list();
    }

    public void acknowledge(Message message) {
        db.sql("DELETE FROM knowledge_backend.index_outbox WHERE id = :id AND dispatch_attempt = :attempt")
                .param("id", message.id()).param("attempt", message.dispatchAttempt()).update();
    }

    public void defer(Message message) {
        int delay = 1 << Math.min(5, message.dispatchAttempt() - 1);
        db.sql("""
                UPDATE knowledge_backend.index_outbox
                SET lease_until = NULL, available_at = now() + make_interval(secs => :delay)
                WHERE id = :id AND dispatch_attempt = :attempt
                """).param("id", message.id()).param("attempt", message.dispatchAttempt())
                .param("delay", delay).update();
    }
}
