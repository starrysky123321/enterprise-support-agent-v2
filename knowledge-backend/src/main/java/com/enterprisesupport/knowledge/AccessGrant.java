package com.enterprisesupport.knowledge;

import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

record AccessGrant(String workspaceId, Role role, UUID accountId, UUID sessionId) {
    static final String ATTRIBUTE = "knowledgeAccessGrant";
    static final String DEFAULT_WORKSPACE = "default";
    static final String RESOURCE_ID = "[a-z0-9][a-z0-9-]{0,63}";

    enum Role { ADMIN, EDITOR, READER, MEMBER }

    boolean admin() {
        return role == Role.ADMIN;
    }

    void requireAdmin() {
        if (!admin()) {
            throw forbidden();
        }
    }

    UUID requireAccount() {
        if (accountId == null) {
            throw forbidden();
        }
        return accountId;
    }

    static ResponseStatusException forbidden() {
        return new ResponseStatusException(HttpStatus.FORBIDDEN, "Insufficient workspace permissions");
    }
}
