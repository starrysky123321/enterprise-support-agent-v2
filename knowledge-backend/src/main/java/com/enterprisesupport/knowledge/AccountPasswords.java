package com.enterprisesupport.knowledge;

import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.UUID;
import java.util.concurrent.Semaphore;
import java.util.function.Supplier;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.HttpStatus;
import org.springframework.security.crypto.argon2.Argon2PasswordEncoder;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.stereotype.Service;

@Service
@ConditionalOnProperty(name = "knowledge.api-enabled", havingValue = "true", matchIfMissing = true)
final class AccountPasswords {
    private final Argon2PasswordEncoder argon = Argon2PasswordEncoder.defaultsForSpringSecurity_v5_8();
    private final BCryptPasswordEncoder bcrypt = new BCryptPasswordEncoder();
    private final String dummyHash = argon.encode(TokenDigest.issue(""));
    private final Semaphore hashSlots = new Semaphore(4);
    private final AccountCatalog accounts;
    private final WorkspaceRateLimit rateLimit;

    AccountPasswords(AccountCatalog accounts, WorkspaceRateLimit rateLimit) {
        this.accounts = accounts;
        this.rateLimit = rateLimit;
    }

    AccountCatalog.Profile register(String email, String password) {
        String normalized = email.toLowerCase(Locale.ROOT);
        return withHashSlot(() -> accounts.register(normalized, argon.encode(password)));
    }

    AccountCatalog.Session login(String email, String password) {
        String normalized = email.toLowerCase(Locale.ROOT);
        rateLimit.acquire(TokenDigest.sha256(normalized), WorkspaceRateLimit.Operation.LOGIN);
        return withHashSlot(() -> {
            var account = accounts.credentials(normalized).orElse(null);
            String digest = account == null ? dummyHash : account.passwordHash();
            if (!matches(password, digest) || account == null || !account.active()) {
                throw AccountCatalog.invalidCredentials();
            }
            return accounts.issueSession(account, digest.startsWith("$2") ? argon.encode(password) : null);
        });
    }

    void changePassword(UUID accountId, String currentPassword, String newPassword) {
        rateLimit.acquire(accountId.toString(), WorkspaceRateLimit.Operation.LOGIN);
        withHashSlot(() -> {
            var current = accounts.credentials(accountId);
            if (!matches(currentPassword, current.passwordHash())) {
                throw AccountCatalog.invalidCredentials();
            }
            accounts.changePassword(current, argon.encode(newPassword));
            return null;
        });
    }

    void resetPassword(UUID accountId, String newPassword) {
        withHashSlot(() -> {
            accounts.resetPassword(accountId, argon.encode(newPassword));
            return null;
        });
    }

    private boolean matches(String password, String digest) {
        if (digest.startsWith("$argon2")) {
            return argon.matches(password, digest);
        }
        return digest.startsWith("$2") && password.getBytes(StandardCharsets.UTF_8).length <= 72
                && bcrypt.matches(password, digest);
    }

    private <T> T withHashSlot(Supplier<T> action) {
        if (!hashSlots.tryAcquire()) {
            throw new WorkspaceRateLimit.Rejected(HttpStatus.SERVICE_UNAVAILABLE, 1, "Password service at capacity");
        }
        try {
            return action.get();
        } finally {
            hashSlots.release();
        }
    }
}
