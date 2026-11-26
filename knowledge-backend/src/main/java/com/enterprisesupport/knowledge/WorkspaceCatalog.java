package com.enterprisesupport.knowledge;

import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;
import org.springframework.web.server.ResponseStatusException;

@Repository
class WorkspaceCatalog {
    private final JdbcClient db;

    WorkspaceCatalog(JdbcClient db) {
        this.db = db;
    }

    record IssuedKey(UUID id, String token, AccessGrant.Role role) {
    }

    record KeySummary(UUID id, AccessGrant.Role role, Instant createdAt) {
    }

    record Workspace(String workspaceId, AccessGrant.Role role) {
    }

    record Member(UUID accountId, String email, AccessGrant.Role role) {
    }

    void create(String workspaceId) {
        db.sql("INSERT INTO knowledge_backend.workspaces (id) VALUES (:id) ON CONFLICT DO NOTHING")
                .param("id", workspaceId).update();
    }

    boolean exists(String workspaceId) {
        return db.sql("SELECT EXISTS (SELECT 1 FROM knowledge_backend.workspaces WHERE id = :id)")
                .param("id", workspaceId).query(Boolean.class).single();
    }

    String requireAccess(AccessGrant grant, String requested, boolean write) {
        String workspace = requested == null ? AccessGrant.DEFAULT_WORKSPACE : requested;
        if (grant.admin()) {
            return workspace;
        }
        boolean allowed = grant.accountId() == null
                ? workspace.equals(grant.workspaceId()) && (!write || grant.role() == AccessGrant.Role.EDITOR)
                : db.sql("""
                        SELECT EXISTS (
                            SELECT 1 FROM knowledge_backend.workspace_members
                            WHERE workspace_id = :workspace AND account_id = :account
                              AND (NOT :write OR role = 'EDITOR')
                        )
                        """).param("workspace", workspace).param("account", grant.accountId()).param("write", write)
                        .query(Boolean.class).single();
        if (!allowed) {
            throw AccessGrant.forbidden();
        }
        return workspace;
    }

    List<Workspace> visibleTo(AccessGrant grant) {
        if (grant.admin()) {
            return db.sql("SELECT id AS workspace_id, 'ADMIN' AS role FROM knowledge_backend.workspaces ORDER BY id")
                    .query(Workspace.class).list();
        }
        if (grant.accountId() == null) {
            return List.of(new Workspace(grant.workspaceId(), grant.role()));
        }
        return db.sql("""
                SELECT workspace_id, role FROM knowledge_backend.workspace_members
                WHERE account_id = :account ORDER BY workspace_id
                """).param("account", grant.accountId()).query(Workspace.class).list();
    }

    List<Member> members(String workspaceId) {
        return db.sql("""
                SELECT a.id AS account_id, a.email, m.role
                FROM knowledge_backend.workspace_members m
                JOIN knowledge_backend.accounts a ON a.id = m.account_id
                WHERE m.workspace_id = :workspace ORDER BY a.email
                """).param("workspace", workspaceId).query(Member.class).list();
    }

    void putMember(String workspaceId, UUID accountId, AccessGrant.Role role) {
        int updated = db.sql("""
                INSERT INTO knowledge_backend.workspace_members (workspace_id, account_id, role)
                SELECT :workspace, id, :role FROM knowledge_backend.accounts WHERE id = :account
                ON CONFLICT (workspace_id, account_id) DO UPDATE SET role = EXCLUDED.role
                """).param("workspace", workspaceId).param("account", accountId)
                .param("role", role.name()).update();
        if (updated == 0) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "Account not found");
        }
    }

    void removeMember(String workspaceId, UUID accountId) {
        db.sql("""
                DELETE FROM knowledge_backend.workspace_members WHERE workspace_id = :workspace AND account_id = :account
                """).param("workspace", workspaceId).param("account", accountId).update();
    }

    List<KeySummary> listKeys(String workspaceId) {
        return db.sql("""
                SELECT id, role, created_at FROM knowledge_backend.workspace_keys
                WHERE workspace_id = :workspace ORDER BY created_at DESC
                """).param("workspace", workspaceId).query(KeySummary.class).list();
    }

    IssuedKey issue(String workspaceId, AccessGrant.Role role) {
        String token = TokenDigest.issue("kb_");
        UUID id = UUID.randomUUID();
        db.sql("""
                INSERT INTO knowledge_backend.workspace_keys (id, workspace_id, token_hash, role)
                VALUES (:id, :workspace, :digest, :role)
                """).param("id", id).param("workspace", workspaceId)
                .param("digest", TokenDigest.sha256(token)).param("role", role.name()).update();
        return new IssuedKey(id, token, role);
    }

    void revoke(String workspaceId, UUID id) {
        db.sql("DELETE FROM knowledge_backend.workspace_keys WHERE id = :id AND workspace_id = :workspace")
                .param("id", id).param("workspace", workspaceId).update();
    }

    Optional<AccessGrant> authenticate(String token) {
        return db.sql("SELECT workspace_id, role FROM knowledge_backend.workspace_keys WHERE token_hash = :digest")
                .param("digest", TokenDigest.sha256(token))
                .query((rs, row) -> new AccessGrant(rs.getString("workspace_id"),
                        AccessGrant.Role.valueOf(rs.getString("role")), null, null)).optional();
    }
}
