package com.enterprisesupport.knowledge.controller;

import com.enterprisesupport.knowledge.config.KnowledgeProperties;
import com.enterprisesupport.knowledge.repository.VersionCatalog;
import com.enterprisesupport.knowledge.repository.WorkspaceCatalog;
import com.enterprisesupport.knowledge.security.AccessGrant;
import com.enterprisesupport.knowledge.service.IndexClient;
import com.enterprisesupport.knowledge.service.PdfBlobStore;
import com.enterprisesupport.knowledge.service.QueryCapacity;
import com.enterprisesupport.knowledge.service.SearchResultCache;
import com.enterprisesupport.knowledge.service.WorkspaceRateLimit;

import static com.enterprisesupport.knowledge.security.AccessGrant.ATTRIBUTE;
import static com.enterprisesupport.knowledge.security.AccessGrant.RESOURCE_ID;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Semaphore;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ProblemDetail;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.CannotGetJdbcConnectionException;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RequestPart;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.servlet.mvc.method.annotation.StreamingResponseBody;

@RestController
@ConditionalOnProperty(name = "knowledge.api-enabled", havingValue = "true", matchIfMissing = true)
@RequestMapping({"/api/knowledge", "/api/knowledge/workspaces/{workspaceId}"})
public final class DocumentController {
    private static final String DOCUMENT = "/documents/{documentId}";
    private final VersionCatalog catalog;
    private final WorkspaceCatalog workspaces;
    private final IndexClient index;
    private final QueryCapacity capacity;
    private final SearchResultCache searchCache;
    private final PdfBlobStore blobStore;
    private final ObjectMapper mapper;
    private final Semaphore pdfUploads;

    public DocumentController(VersionCatalog catalog, WorkspaceCatalog workspaces, IndexClient index,
                       QueryCapacity capacity, SearchResultCache searchCache, PdfBlobStore blobStore,
                       ObjectMapper mapper, KnowledgeProperties properties) {
        this.catalog = catalog;
        this.workspaces = workspaces;
        this.index = index;
        this.capacity = capacity;
        this.searchCache = searchCache;
        this.blobStore = blobStore;
        this.mapper = mapper;
        pdfUploads = new Semaphore(properties.pdfUploadConcurrency());
    }

    @PostMapping(path = DOCUMENT + "/versions", consumes = MediaType.APPLICATION_JSON_VALUE)
    @ResponseStatus(HttpStatus.ACCEPTED)
    public VersionCatalog.VersionStatus submit(
            @RequestAttribute(ATTRIBUTE) AccessGrant grant,
            @PathVariable(required = false) @Pattern(regexp = RESOURCE_ID) String workspaceId,
            @PathVariable @Pattern(regexp = RESOURCE_ID) String documentId, @Valid @RequestBody Upload request) {
        return catalog.submit(requireExistingWorkspace(grant, workspaceId, true), documentId, request.text(),
                new VersionCatalog.Metadata(
                        cleanMeta(request.documentType()),
                        cleanMeta(request.productName()),
                        cleanMeta(request.productVersion())));
    }

    @PostMapping(path = DOCUMENT + "/versions/pdf", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    @ResponseStatus(HttpStatus.ACCEPTED)
    public VersionCatalog.VersionStatus submitPdf(
            @RequestAttribute(ATTRIBUTE) AccessGrant grant,
            @PathVariable(required = false) @Pattern(regexp = RESOURCE_ID) String workspaceId,
            @PathVariable @Pattern(regexp = RESOURCE_ID) String documentId,
            @RequestPart("file") MultipartFile file,
            @RequestParam(required = false) @Size(max = 32) String documentType,
            @RequestParam(required = false) @Size(max = 128) String productName,
            @RequestParam(required = false) @Size(max = 64) String productVersion)
            throws IOException {
        String workspace = requireExistingWorkspace(grant, workspaceId, true);
        if (!pdfUploads.tryAcquire()) {
            throw new IndexClient.Failure("PDF upload capacity reached", 503);
        }
        try {
            String original = file.getOriginalFilename();
            String filename = original == null ? "" : original.replace('\\', '/');
            filename = filename.substring(filename.lastIndexOf('/') + 1).replaceAll("\\p{Cntrl}", "").strip();
            if (filename.isBlank() || filename.length() > 255) {
                filename = "document.pdf";
            }
            var metadata = new VersionCatalog.Metadata(
                    cleanMeta(documentType), cleanMeta(productName), cleanMeta(productVersion));
            try (var contents = file.getInputStream(); var staged = blobStore.stage(contents)) {
                return catalog.submitPdf(workspace, documentId, filename, staged, metadata);
            }
        } finally {
            pdfUploads.release();
        }
    }

    @GetMapping("/documents")
    public DocumentPage list(
            @RequestAttribute(ATTRIBUTE) AccessGrant grant,
            @PathVariable(required = false) @Pattern(regexp = RESOURCE_ID) String workspaceId,
            @RequestParam(defaultValue = "") @Pattern(regexp = "(?:" + RESOURCE_ID + ")?") String after,
            @RequestParam(defaultValue = "50") @Min(1) @Max(100) int limit) {
        var documents = catalog.listDocuments(requireExistingWorkspace(grant, workspaceId, false), after, limit + 1);
        var page = List.copyOf(documents.subList(0, Math.min(documents.size(), limit)));
        String cursor = documents.size() > limit ? page.get(limit - 1).documentId() : null;
        return new DocumentPage(page, cursor);
    }

    @DeleteMapping(DOCUMENT)
    @ResponseStatus(HttpStatus.ACCEPTED)
    public void delete(
            @RequestAttribute(ATTRIBUTE) AccessGrant grant,
            @PathVariable(required = false) @Pattern(regexp = RESOURCE_ID) String workspaceId,
            @PathVariable @Pattern(regexp = RESOURCE_ID) String documentId) {
        catalog.deleteDocument(requireExistingWorkspace(grant, workspaceId, true), documentId);
    }

    @GetMapping(DOCUMENT + "/versions/{version}")
    public VersionCatalog.VersionStatus status(
            @RequestAttribute(ATTRIBUTE) AccessGrant grant,
            @PathVariable(required = false) @Pattern(regexp = RESOURCE_ID) String workspaceId,
            @PathVariable @Pattern(regexp = RESOURCE_ID) String documentId, @PathVariable @Positive long version) {
        return catalog.status(workspaces.requireAccess(grant, workspaceId, false), documentId, version)
                .orElseThrow(DocumentController::unknownVersion);
    }

    @PostMapping(DOCUMENT + "/versions/{version}/retry")
    @ResponseStatus(HttpStatus.ACCEPTED)
    public VersionCatalog.VersionStatus retry(
            @RequestAttribute(ATTRIBUTE) AccessGrant grant,
            @PathVariable(required = false) @Pattern(regexp = RESOURCE_ID) String workspaceId,
            @PathVariable @Pattern(regexp = RESOURCE_ID) String documentId, @PathVariable @Positive long version) {
        return catalog.retry(workspaces.requireAccess(grant, workspaceId, true), documentId, version)
                .orElseThrow(DocumentController::unknownVersion);
    }

    @PostMapping(path = DOCUMENT + "/search", consumes = MediaType.APPLICATION_JSON_VALUE)
    public QueryResult search(
            @RequestAttribute(ATTRIBUTE) AccessGrant grant,
            @PathVariable(required = false) @Pattern(regexp = RESOURCE_ID) String workspaceId,
            @PathVariable @Pattern(regexp = RESOURCE_ID) String documentId, @Valid @RequestBody Query request) {
        String workspace = workspaces.requireAccess(grant, workspaceId, false);
        var filter = request.metadataFilter();
        return capacity.search(workspace, () -> {
            var version = published(workspace, documentId);
            var result = searchCache.search(workspace, version.id(), request.query(), filter.fingerprint(),
                    () -> index.search(version.id(), request.query(), filter));
            return new QueryResult(documentId, version.versionNumber(), result);
        });
    }

    @PostMapping(path = DOCUMENT + "/ask", consumes = MediaType.APPLICATION_JSON_VALUE)
    public QueryResult ask(
            @RequestAttribute(ATTRIBUTE) AccessGrant grant,
            @PathVariable(required = false) @Pattern(regexp = RESOURCE_ID) String workspaceId,
            @PathVariable @Pattern(regexp = RESOURCE_ID) String documentId, @Valid @RequestBody Query request) {
        String workspace = workspaces.requireAccess(grant, workspaceId, false);
        var filter = request.metadataFilter();
        return capacity.answer(workspace, () -> {
            var version = published(workspace, documentId);
            return new QueryResult(documentId, version.versionNumber(),
                    index.ask(version.id(), request.query(), filter));
        });
    }

    @PostMapping(path = "/search", consumes = MediaType.APPLICATION_JSON_VALUE)
    public WorkspaceResult searchWorkspace(
            @RequestAttribute(ATTRIBUTE) AccessGrant grant,
            @PathVariable(required = false) @Pattern(regexp = RESOURCE_ID) String workspaceId,
            @Valid @RequestBody Query request) {
        String workspace = requireExistingWorkspace(grant, workspaceId, false);
        return capacity.search(workspace, () -> queryWorkspace(workspace, request, false));
    }

    @PostMapping(path = "/ask", consumes = MediaType.APPLICATION_JSON_VALUE)
    public WorkspaceResult askWorkspace(
            @RequestAttribute(ATTRIBUTE) AccessGrant grant,
            @PathVariable(required = false) @Pattern(regexp = RESOURCE_ID) String workspaceId,
            @Valid @RequestBody Query request) {
        String workspace = requireExistingWorkspace(grant, workspaceId, false);
        return capacity.answer(workspace, () -> queryWorkspace(workspace, request, true));
    }

    @PostMapping(path = DOCUMENT + "/ask/stream", consumes = MediaType.APPLICATION_JSON_VALUE,
            produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public ResponseEntity<StreamingResponseBody> askStream(
            @RequestAttribute(ATTRIBUTE) AccessGrant grant,
            @PathVariable(required = false) @Pattern(regexp = RESOURCE_ID) String workspaceId,
            @PathVariable @Pattern(regexp = RESOURCE_ID) String documentId, @Valid @RequestBody Query request) {
        String workspace = workspaces.requireAccess(grant, workspaceId, false);
        var version = published(workspace, documentId);
        var filter = request.metadataFilter();
        return sseResponse(out -> capacity.answer(workspace, () -> {
            try {
                writeSseEvent(out, "scope", Map.of(
                        "documentId", documentId,
                        "version", version.versionNumber(),
                        "documents", List.of()));
                index.streamAsk(version.id(), request.query(), filter, out);
            } catch (IOException error) {
                throw new IndexClient.Failure("Streaming response failed", 502);
            }
            return null;
        }));
    }

    @PostMapping(path = "/ask/stream", consumes = MediaType.APPLICATION_JSON_VALUE,
            produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public ResponseEntity<StreamingResponseBody> askWorkspaceStream(
            @RequestAttribute(ATTRIBUTE) AccessGrant grant,
            @PathVariable(required = false) @Pattern(regexp = RESOURCE_ID) String workspaceId,
            @Valid @RequestBody Query request) {
        String workspace = requireExistingWorkspace(grant, workspaceId, false);
        var snapshot = catalog.publishedWorkspace(workspace);
        var published = snapshot.documents();
        var ids = published.stream().map(VersionCatalog.PublishedDocument::indexId).toList();
        var filter = request.metadataFilter();
        return sseResponse(out -> capacity.answer(workspace, () -> {
            try {
                writeSseEvent(out, "scope", Map.of("workspaceId", workspace, "documents", published));
                index.streamAsk(ids, request.query(), filter, out);
            } catch (IOException error) {
                throw new IndexClient.Failure("Streaming response failed", 502);
            }
            return null;
        }));
    }

    private ResponseEntity<StreamingResponseBody> sseResponse(StreamingResponseBody body) {
        return ResponseEntity.ok()
                .contentType(MediaType.TEXT_EVENT_STREAM)
                .header(HttpHeaders.CACHE_CONTROL, "no-cache")
                .header("X-Accel-Buffering", "no")
                .body(body);
    }

    private void writeSseEvent(OutputStream out, String event, Object payload) throws IOException {
        String frame = "event: " + event + "\ndata: " + mapper.writeValueAsString(payload) + "\n\n";
        out.write(frame.getBytes(StandardCharsets.UTF_8));
        out.flush();
    }

    private WorkspaceResult queryWorkspace(String workspace, Query request, boolean answer) {
        var snapshot = catalog.publishedWorkspace(workspace);
        var published = snapshot.documents();
        var ids = published.stream().map(VersionCatalog.PublishedDocument::indexId).toList();
        var filter = request.metadataFilter();
        var result = answer
                ? index.ask(ids, request.query(), filter)
                : searchCache.searchWorkspace(workspace, snapshot.catalogVersion(), ids, request.query(),
                        filter.fingerprint(), () -> index.search(ids, request.query(), filter));
        var used = new HashSet<String>();
        result.path(answer ? "citations" : "chunks").forEach(item -> used.add(item.path("doc_id").asText()));
        var sources = published.stream().filter(document -> used.contains(document.indexId().toString())).toList();
        return new WorkspaceResult(workspace, result, sources);
    }

    private String requireExistingWorkspace(AccessGrant grant, String workspaceId, boolean write) {
        String workspace = workspaces.requireAccess(grant, workspaceId, write);
        if (grant.admin() && !workspaces.exists(workspace)) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "Workspace not found");
        }
        return workspace;
    }

    @ExceptionHandler(WorkspaceRateLimit.Rejected.class)
    public ResponseEntity<ProblemDetail> quotaRejected(WorkspaceRateLimit.Rejected rejection) {
        return ResponseEntity.status(rejection.status).header(HttpHeaders.RETRY_AFTER, Long.toString(rejection.retryAfter))
                .body(ProblemDetail.forStatusAndDetail(rejection.status, rejection.getMessage()));
    }

    @ExceptionHandler(IndexClient.Failure.class)
    public ResponseEntity<ProblemDetail> indexUnavailable(IndexClient.Failure failure) {
        if (failure.status() == 429 || failure.status() == 503) {
            return unavailable(failure.getMessage());
        }
        return ResponseEntity.status(HttpStatus.BAD_GATEWAY)
                .body(ProblemDetail.forStatusAndDetail(HttpStatus.BAD_GATEWAY, failure.getMessage()));
    }

    @ExceptionHandler(CannotGetJdbcConnectionException.class)
    public ResponseEntity<ProblemDetail> databaseUnavailable() {
        return unavailable("Database capacity unavailable");
    }

    private static ResponseEntity<ProblemDetail> unavailable(String detail) {
        return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE).header(HttpHeaders.RETRY_AFTER, "1")
                .body(ProblemDetail.forStatusAndDetail(HttpStatus.SERVICE_UNAVAILABLE, detail));
    }

    private VersionCatalog.PublishedVersion published(String workspaceId, String documentId) {
        return catalog.published(workspaceId, documentId).orElseThrow(() ->
                new ResponseStatusException(HttpStatus.CONFLICT, "Document has no published version"));
    }

    private static ResponseStatusException unknownVersion() {
        return new ResponseStatusException(HttpStatus.NOT_FOUND, "Document version not found");
    }

    private static String cleanMeta(String value) {
        if (value == null) {
            return null;
        }
        String trimmed = value.strip();
        return trimmed.isEmpty() ? null : trimmed;
    }

    public record Upload(@NotBlank @Size(max = 100_000) @Pattern(regexp = "[^\\x00]*") String text,
                  @Size(max = 32) String documentType,
                  @Size(max = 128) String productName,
                  @Size(max = 64) String productVersion) {
    }

    public record Query(@NotBlank @Size(max = 4096) String query,
                 List<@Size(max = 32) String> documentTypes,
                 @Size(max = 128) String productName,
                 @Size(max = 64) String productVersion) {
        IndexClient.MetadataFilter metadataFilter() {
            List<String> types = documentTypes == null
                    ? null
                    : documentTypes.stream().map(DocumentController::cleanMeta)
                            .filter(v -> v != null).distinct().toList();
            return new IndexClient.MetadataFilter(
                    (types == null || types.isEmpty()) ? null : types,
                    cleanMeta(productName),
                    cleanMeta(productVersion));
        }
    }

    public record QueryResult(String documentId, long version, JsonNode result) {
    }

    public record DocumentPage(List<VersionCatalog.DocumentSummary> items, String nextCursor) {
    }

    public record WorkspaceResult(String workspaceId, JsonNode result, List<VersionCatalog.PublishedDocument> documents) {
    }
}
