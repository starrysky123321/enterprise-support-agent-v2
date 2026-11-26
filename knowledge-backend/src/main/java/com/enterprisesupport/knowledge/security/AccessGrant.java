package com.enterprisesupport.knowledge.security;

import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

public record AccessGrant(String workspaceId, Role role, UUID accountId, UUID sessionId) {
    public static final String ATTRIBUTE = "knowledgeAccessGrant";
    public static final String DEFAULT_WORKSPACE = "default";
    public static final String RESOURCE_ID = "[a-z0-9][a-z0-9-]{0,63}";

    public enum Role { ADMIN, EDITOR, READER, MEMBER }

    public boolean admin() {
        return role == Role.ADMIN;
    }

    public void requireAdmin() {
        if (!admin()) {
            throw forbidden();
        }
    }

    public UUID requireAccount() {
        if (accountId == null) {
            throw forbidden();
        }
        return accountId;
    }

    public static ResponseStatusException forbidden() {
        return new ResponseStatusException(HttpStatus.FORBIDDEN, "Insufficient workspace permissions");
    }
}
