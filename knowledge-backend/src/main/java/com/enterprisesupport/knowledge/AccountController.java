package com.enterprisesupport.knowledge;

import static com.enterprisesupport.knowledge.AccessGrant.ATTRIBUTE;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import java.time.Duration;
import java.util.List;
import java.util.UUID;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ProblemDetail;
import org.springframework.http.ResponseCookie;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/knowledge")
@ConditionalOnProperty(name = "knowledge.api-enabled", havingValue = "true", matchIfMissing = true)
final class AccountController {
    private final AccountCatalog accounts;
    private final AccountPasswords passwords;

    AccountController(AccountCatalog accounts, AccountPasswords passwords) {
        this.accounts = accounts;
        this.passwords = passwords;
    }

    @PostMapping("/auth/register")
    ResponseEntity<AccountCatalog.Profile> register(@Valid @RequestBody Registration request) {
        return ResponseEntity.status(HttpStatus.CREATED).cacheControl(CacheControl.noStore())
                .body(passwords.register(request.email(), request.password()));
    }

    @PostMapping("/auth/login")
    ResponseEntity<AccountCatalog.Session> login(@Valid @RequestBody Login request, HttpServletRequest http) {
        var session = passwords.login(request.email(), request.password());
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .header(HttpHeaders.SET_COOKIE, sessionCookie(session.accessToken(), Duration.ofHours(12), http).toString())
                .body(session);
    }

    @PostMapping("/auth/logout")
    ResponseEntity<Void> logout(@RequestAttribute(ATTRIBUTE) AccessGrant grant, HttpServletRequest http) {
        grant.requireAccount();
        accounts.logout(grant.sessionId());
        return ResponseEntity.noContent()
                .header(HttpHeaders.SET_COOKIE, sessionCookie("", Duration.ZERO, http).toString())
                .build();
    }

    @GetMapping("/users/me")
    ResponseEntity<AccountCatalog.Profile> profile(@RequestAttribute(ATTRIBUTE) AccessGrant grant) {
        return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(accounts.profile(grant.requireAccount()));
    }

    @PutMapping("/users/me/password")
    ResponseEntity<Void> changePassword(@RequestAttribute(ATTRIBUTE) AccessGrant grant,
                                        @Valid @RequestBody PasswordChange request, HttpServletRequest http) {
        passwords.changePassword(grant.requireAccount(), request.currentPassword(), request.newPassword());
        return ResponseEntity.noContent()
                .header(HttpHeaders.SET_COOKIE, sessionCookie("", Duration.ZERO, http).toString())
                .build();
    }

    private static ResponseCookie sessionCookie(String value, Duration maxAge, HttpServletRequest http) {
        boolean secure = http.isSecure() || "https".equalsIgnoreCase(http.getHeader("X-Forwarded-Proto"));
        return ResponseCookie.from(ApiTokenFilter.SESSION_COOKIE, value)
                .httpOnly(true)
                .secure(secure)
                .sameSite("Lax")
                .path("/api/knowledge")
                .maxAge(maxAge)
                .build();
    }

    @GetMapping("/users")
    AccountPage list(@RequestAttribute(ATTRIBUTE) AccessGrant grant, @RequestParam(required = false) UUID after,
                     @RequestParam(defaultValue = "50") @Min(1) @Max(100) int limit) {
        grant.requireAdmin();
        var rows = accounts.list(after, limit + 1);
        return new AccountPage(List.copyOf(rows.subList(0, Math.min(rows.size(), limit))),
                rows.size() > limit ? rows.get(limit - 1).id() : null);
    }

    @PutMapping("/users/{accountId}/status")
    AccountCatalog.Profile setActive(@RequestAttribute(ATTRIBUTE) AccessGrant grant, @PathVariable UUID accountId,
                                     @Valid @RequestBody Status request) {
        grant.requireAdmin();
        return accounts.setActive(accountId, request.active());
    }

    @PutMapping("/users/{accountId}/department")
    AccountCatalog.Profile setDepartment(@RequestAttribute(ATTRIBUTE) AccessGrant grant, @PathVariable UUID accountId,
                                         @Valid @RequestBody Department request) {
        grant.requireAdmin();
        return accounts.setDepartment(accountId, request.department());
    }

    @PutMapping("/users/{accountId}/password")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    void resetPassword(@RequestAttribute(ATTRIBUTE) AccessGrant grant, @PathVariable UUID accountId,
                       @Valid @RequestBody PasswordReset request) {
        grant.requireAdmin();
        passwords.resetPassword(accountId, request.newPassword());
    }

    @ExceptionHandler(WorkspaceRateLimit.Rejected.class)
    ResponseEntity<ProblemDetail> quotaRejected(WorkspaceRateLimit.Rejected rejection) {
        return ResponseEntity.status(rejection.status).header(HttpHeaders.RETRY_AFTER, Long.toString(rejection.retryAfter))
                .body(ProblemDetail.forStatusAndDetail(rejection.status, rejection.getMessage()));
    }

    @ExceptionHandler({MethodArgumentNotValidException.class, HttpMessageNotReadableException.class})
    ResponseEntity<ProblemDetail> invalidRequest() {
        return ResponseEntity.badRequest()
                .body(ProblemDetail.forStatusAndDetail(HttpStatus.BAD_REQUEST, "Invalid account request"));
    }

    record Registration(@NotBlank @Email @Size(max = 320) String email,
                        @NotBlank @Size(min = 12, max = 256) String password) {
    }

    record Login(@NotBlank @Email @Size(max = 320) String email,
                 @NotBlank @Size(max = 1024) String password) {
    }

    record PasswordChange(@NotBlank @Size(max = 1024) String currentPassword,
                          @NotBlank @Size(min = 12, max = 256) String newPassword) {
    }

    record PasswordReset(@NotBlank @Size(min = 12, max = 256) String newPassword) {
    }

    record Status(@NotNull Boolean active) {
    }

    record Department(@Size(max = 255) String department) {
    }

    record AccountPage(List<AccountCatalog.Profile> items, UUID nextCursor) {
    }
}
