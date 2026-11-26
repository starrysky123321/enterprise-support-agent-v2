package com.enterprisesupport.knowledge.controller;

import com.enterprisesupport.knowledge.repository.WorkspaceCatalog;
import com.enterprisesupport.knowledge.security.AccessGrant;

import static com.enterprisesupport.knowledge.security.AccessGrant.ATTRIBUTE;
import static com.enterprisesupport.knowledge.security.AccessGrant.RESOURCE_ID;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import java.util.List;
import java.util.UUID;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

@RestController
@ConditionalOnProperty(name = "knowledge.api-enabled", havingValue = "true", matchIfMissing = true)
@RequestMapping("/api/knowledge/workspaces")
public final class WorkspaceController {
    private final WorkspaceCatalog workspaces;

    public WorkspaceController(WorkspaceCatalog workspaces) {
        this.workspaces = workspaces;
    }

    @GetMapping
    public List<WorkspaceCatalog.Workspace> list(@RequestAttribute(ATTRIBUTE) AccessGrant grant) {
        return workspaces.visibleTo(grant);
    }

    @PutMapping("/{workspaceId}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void create(@RequestAttribute(ATTRIBUTE) AccessGrant grant,
                @PathVariable @Pattern(regexp = RESOURCE_ID) String workspaceId) {
        grant.requireAdmin();
        workspaces.create(workspaceId);
    }

    @GetMapping("/{workspaceId}/keys")
    public List<WorkspaceCatalog.KeySummary> listKeys(
            @RequestAttribute(ATTRIBUTE) AccessGrant grant,
            @PathVariable @Pattern(regexp = RESOURCE_ID) String workspaceId) {
        grant.requireAdmin();
        requireExists(workspaceId);
        return workspaces.listKeys(workspaceId);
    }

    @PostMapping("/{workspaceId}/keys")
    public ResponseEntity<WorkspaceCatalog.IssuedKey> issue(
            @RequestAttribute(ATTRIBUTE) AccessGrant grant,
            @PathVariable @Pattern(regexp = RESOURCE_ID) String workspaceId, @Valid @RequestBody KeyRequest request) {
        grant.requireAdmin();
        requireWorkspaceRole(request.role());
        requireExists(workspaceId);
        return ResponseEntity.status(HttpStatus.CREATED).cacheControl(CacheControl.noStore())
                .body(workspaces.issue(workspaceId, request.role()));
    }

    @DeleteMapping("/{workspaceId}/keys/{id}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void revoke(@RequestAttribute(ATTRIBUTE) AccessGrant grant,
                @PathVariable @Pattern(regexp = RESOURCE_ID) String workspaceId, @PathVariable UUID id) {
        grant.requireAdmin();
        workspaces.revoke(workspaceId, id);
    }

    @GetMapping("/{workspaceId}/members")
    public List<WorkspaceCatalog.Member> members(@RequestAttribute(ATTRIBUTE) AccessGrant grant,
                                         @PathVariable @Pattern(regexp = RESOURCE_ID) String workspaceId) {
        grant.requireAdmin();
        requireExists(workspaceId);
        return workspaces.members(workspaceId);
    }

    @PutMapping("/{workspaceId}/members/{accountId}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void putMember(@RequestAttribute(ATTRIBUTE) AccessGrant grant,
                   @PathVariable @Pattern(regexp = RESOURCE_ID) String workspaceId, @PathVariable UUID accountId,
                   @Valid @RequestBody KeyRequest request) {
        grant.requireAdmin();
        requireWorkspaceRole(request.role());
        requireExists(workspaceId);
        workspaces.putMember(workspaceId, accountId, request.role());
    }

    @DeleteMapping("/{workspaceId}/members/{accountId}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void removeMember(@RequestAttribute(ATTRIBUTE) AccessGrant grant,
                      @PathVariable @Pattern(regexp = RESOURCE_ID) String workspaceId, @PathVariable UUID accountId) {
        grant.requireAdmin();
        workspaces.removeMember(workspaceId, accountId);
    }

    private void requireExists(String workspaceId) {
        if (!workspaces.exists(workspaceId)) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "Workspace not found");
        }
    }

    private static void requireWorkspaceRole(AccessGrant.Role role) {
        if (role != AccessGrant.Role.READER && role != AccessGrant.Role.EDITOR) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Workspace role must be READER or EDITOR");
        }
    }

    public record KeyRequest(@NotNull AccessGrant.Role role) {
    }
}
