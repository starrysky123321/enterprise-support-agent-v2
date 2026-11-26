package com.enterprisesupport.knowledge;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

@Repository
class AccountCatalog {
    private static final String PROFILE = """
            SELECT id, email, is_active AS active, is_admin AS admin, is_verified AS verified, department
            FROM knowledge_backend.accounts
            """;
    private final JdbcClient db;

    AccountCatalog(JdbcClient db) {
        this.db = db;
    }

    record Profile(UUID id, String email, boolean active, boolean admin, boolean verified, String department) {
    }

    record Credentials(UUID id, String passwordHash, boolean active) {
    }

    record Session(String accessToken, String tokenType, OffsetDateTime expiresAt) {
    }

    Profile register(String email, String passwordHash) {
        UUID id = UUID.randomUUID();
        int created = db.sql("""
                INSERT INTO knowledge_backend.accounts (id, email, password_hash)
                VALUES (:id, :email, :passwordHash) ON CONFLICT (email) DO NOTHING
                """).param("id", id).param("email", email).param("passwordHash", passwordHash).update();
        if (created == 0) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "Email is already registered");
        }
        return profile(id);
    }

    Profile profile(UUID id) {
        return db.sql(PROFILE + " WHERE id = :id").param("id", id).query(Profile.class).optional()
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "Account not found"));
    }

    List<Profile> list(UUID after, int limit) {
        return db.sql(PROFILE + " WHERE (CAST(:after AS uuid) IS NULL OR id > :after) ORDER BY id LIMIT :limit")
                .param("after", after, java.sql.Types.OTHER).param("limit", limit).query(Profile.class).list();
    }

    Optional<Credentials> credentials(String email) {
        return db.sql("""
                SELECT id, password_hash, is_active AS active FROM knowledge_backend.accounts WHERE email = :email
                """).param("email", email).query(Credentials.class).optional();
    }

    Credentials credentials(UUID id) {
        return db.sql("""
                SELECT id, password_hash, is_active AS active FROM knowledge_backend.accounts WHERE id = :id
                """).param("id", id).query(Credentials.class).single();
    }

    @Transactional
    public Session issueSession(Credentials credentials, String upgradedHash) {
        db.sql("""
                SELECT id FROM knowledge_backend.accounts
                WHERE id = :id AND is_active AND password_hash = :passwordHash FOR UPDATE
                """).param("id", credentials.id()).param("passwordHash", credentials.passwordHash()).query(UUID.class)
                .optional().orElseThrow(AccountCatalog::invalidCredentials);
        if (upgradedHash != null) {
            db.sql("UPDATE knowledge_backend.accounts SET password_hash = :hash WHERE id = :id")
                    .param("hash", upgradedHash).param("id", credentials.id()).update();
        }
        String token = TokenDigest.issue("ks_");
        OffsetDateTime expiresAt = db.sql("""
                INSERT INTO knowledge_backend.account_sessions (id, account_id, token_hash, expires_at)
                VALUES (:id, :account, :digest, now() + interval '12 hours') RETURNING expires_at
                """).param("id", UUID.randomUUID()).param("account", credentials.id())
                .param("digest", TokenDigest.sha256(token)).query(OffsetDateTime.class).single();
        return new Session(token, "Bearer", expiresAt);
    }

    Optional<AccessGrant> authenticate(String token) {
        return db.sql("""
                SELECT a.id, a.is_admin, s.id AS session_id
                FROM knowledge_backend.account_sessions s
                JOIN knowledge_backend.accounts a ON a.id = s.account_id
                WHERE s.token_hash = :digest AND s.expires_at > now() AND a.is_active
                """).param("digest", TokenDigest.sha256(token))
                .query((row, number) -> new AccessGrant(null,
                        row.getBoolean("is_admin") ? AccessGrant.Role.ADMIN : AccessGrant.Role.MEMBER,
                        row.getObject("id", UUID.class), row.getObject("session_id", UUID.class))).optional();
    }

    void logout(UUID sessionId) {
        db.sql("DELETE FROM knowledge_backend.account_sessions WHERE id = :id").param("id", sessionId).update();
    }

    @Transactional
    public void changePassword(Credentials credentials, String passwordHash) {
        int changed = db.sql("""
                UPDATE knowledge_backend.accounts SET password_hash = :newHash
                WHERE id = :id AND is_active AND password_hash = :oldHash
                """).param("newHash", passwordHash).param("id", credentials.id())
                .param("oldHash", credentials.passwordHash()).update();
        if (changed == 0) {
            throw invalidCredentials();
        }
        revokeSessions(credentials.id());
    }

    @Transactional
    public void resetPassword(UUID accountId, String passwordHash) {
        int changed = db.sql("UPDATE knowledge_backend.accounts SET password_hash = :newHash WHERE id = :id")
                .param("newHash", passwordHash).param("id", accountId).update();
        if (changed == 0) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "Account not found");
        }
        revokeSessions(accountId);
    }

    @Transactional
    public Profile setActive(UUID accountId, boolean active) {
        db.sql("UPDATE knowledge_backend.accounts SET is_active = :active WHERE id = :id")
                .param("active", active).param("id", accountId).update();
        if (!active) {
            revokeSessions(accountId);
        }
        return profile(accountId);
    }

    Profile setDepartment(UUID accountId, String department) {
        db.sql("UPDATE knowledge_backend.accounts SET department = :department WHERE id = :id")
                .param("department", department).param("id", accountId).update();
        return profile(accountId);
    }

    private void revokeSessions(UUID accountId) {
        db.sql("DELETE FROM knowledge_backend.account_sessions WHERE account_id = :id")
                .param("id", accountId).update();
    }

    @Scheduled(fixedDelay = 900_000)
    void deleteExpiredSessions() {
        db.sql("""
                DELETE FROM knowledge_backend.account_sessions WHERE id IN (
                    SELECT id FROM knowledge_backend.account_sessions WHERE expires_at <= now()
                    ORDER BY expires_at LIMIT 10000 FOR UPDATE SKIP LOCKED
                )
                """).update();
    }

    static ResponseStatusException invalidCredentials() {
        return new ResponseStatusException(HttpStatus.UNAUTHORIZED, "Invalid credentials");
    }
}
