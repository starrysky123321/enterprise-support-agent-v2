package com.enterprisesupport.knowledge.security;

import com.enterprisesupport.knowledge.config.KnowledgeProperties;
import com.enterprisesupport.knowledge.repository.AccountCatalog;
import com.enterprisesupport.knowledge.repository.WorkspaceCatalog;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.Cookie;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import org.springframework.dao.DataAccessException;
import org.springframework.http.HttpHeaders;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

@Component
public final class ApiTokenFilter extends OncePerRequestFilter {
    public static final String SESSION_COOKIE = "knowledge_session";
    private static final String PREFIX = "Bearer ";
    private final byte[] authorization;
    private final WorkspaceCatalog workspaces;
    private final AccountCatalog accounts;

    public ApiTokenFilter(KnowledgeProperties properties, WorkspaceCatalog workspaces, AccountCatalog accounts) {
        this.workspaces = workspaces;
        this.accounts = accounts;
        authorization = (PREFIX + properties.apiToken()).getBytes(StandardCharsets.UTF_8);
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        String path = request.getServletPath();
        if ("POST".equals(request.getMethod()) && (path.equals("/api/knowledge/auth/register")
                || path.equals("/api/knowledge/auth/login"))) {
            return true;
        }
        return "GET".equals(request.getMethod()) && (path.equals("/actuator/health")
                || path.equals("/actuator/health/liveness") || path.equals("/actuator/health/readiness"));
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws IOException, ServletException {
        String header = request.getHeader(HttpHeaders.AUTHORIZATION);
        AccessGrant grant;
        try {
            grant = authenticate(header, request.getCookies());
        } catch (DataAccessException error) {
            response.setHeader(HttpHeaders.RETRY_AFTER, "1");
            response.sendError(HttpServletResponse.SC_SERVICE_UNAVAILABLE);
            return;
        }
        if (grant == null) {
            response.setHeader(HttpHeaders.WWW_AUTHENTICATE, "Bearer");
            response.sendError(HttpServletResponse.SC_UNAUTHORIZED);
            return;
        }
        if (request.getServletPath().startsWith("/actuator/") && !grant.admin()) {
            response.sendError(HttpServletResponse.SC_FORBIDDEN);
            return;
        }
        request.setAttribute(AccessGrant.ATTRIBUTE, grant);
        chain.doFilter(request, response);
    }

    private AccessGrant authenticate(String header, Cookie[] cookies) {
        if (header != null && header.startsWith(PREFIX)) {
            if (MessageDigest.isEqual(authorization, header.getBytes(StandardCharsets.UTF_8))) {
                return new AccessGrant(null, AccessGrant.Role.ADMIN, null, null);
            }
            String token = header.substring(PREFIX.length());
            if (token.length() != 46) {
                return null;
            }
            if (token.startsWith("ks_")) {
                return accounts.authenticate(token).orElse(null);
            }
            return token.startsWith("kb_") ? workspaces.authenticate(token).orElse(null) : null;
        }
        if (cookies != null) {
            for (Cookie cookie : cookies) {
                if (SESSION_COOKIE.equals(cookie.getName())) {
                    String token = cookie.getValue();
                    if (token != null && token.length() == 46 && token.startsWith("ks_")) {
                        return accounts.authenticate(token).orElse(null);
                    }
                }
            }
        }
        return null;
    }
}
