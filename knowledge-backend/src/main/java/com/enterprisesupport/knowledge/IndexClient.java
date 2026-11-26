package com.enterprisesupport.knowledge;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.List;
import java.util.UUID;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.http.client.MultipartBodyBuilder;
import org.springframework.http.MediaType;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestClientResponseException;

@Component
final class IndexClient {
    private final RestClient searchHttp;
    private final RestClient longRunningHttp;
    private final HttpClient streamHttp;
    private final URI indexBaseUri;
    private final String bearerToken;
    private final ObjectMapper mapper;
    private final PdfBlobStore blobStore;

    IndexClient(RestClient.Builder builder, KnowledgeProperties properties, ObjectMapper mapper,
                PdfBlobStore blobStore) {
        this.mapper = mapper;
        this.blobStore = blobStore;
        this.indexBaseUri = properties.indexUrl();
        this.bearerToken = "Bearer " + properties.indexToken();
        var client = HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(Duration.ofSeconds(2)).build();
        this.streamHttp = client;
        builder.baseUrl(properties.indexUrl().toString())
                .defaultHeaders(headers -> headers.setBearerAuth(properties.indexToken()));
        searchHttp = withDeadline(builder.clone(), client, Duration.ofSeconds(5));
        longRunningHttp = withDeadline(builder.clone(), client, Duration.ofSeconds(60));
    }

    private static RestClient withDeadline(RestClient.Builder builder, HttpClient client, Duration timeout) {
        var factory = new JdkClientHttpRequestFactory(client);
        factory.setReadTimeout(timeout);
        return builder
                .requestFactory(factory)
                .build();
    }

    record MetadataFilter(List<String> documentTypes, String productName, String productVersion) {
        static final MetadataFilter EMPTY = new MetadataFilter(null, null, null);

        String fingerprint() {
            String types = (documentTypes == null || documentTypes.isEmpty())
                    ? ""
                    : documentTypes.stream().sorted().reduce((a, b) -> a + "," + b).orElse("");
            String product = productName == null ? "" : productName;
            String version = productVersion == null ? "" : productVersion;
            if (types.isEmpty() && product.isEmpty() && version.isEmpty()) {
                return "";
            }
            return types + "|" + product + "|" + version;
        }
    }

    void index(VersionCatalog.IndexJob job) {
        JsonNode result;
        if (job.pdf() == null && job.pdfStorageKey() == null) {
            result = post(longRunningHttp, "/internal/index", new IndexRequest(
                    job.id(), job.documentId(), job.body(),
                    job.documentType(), job.productName(), job.productVersion()));
        } else {
            Resource pdfResource;
            if (job.pdfStorageKey() != null) {
                var path = blobStore.resolve(job.pdfStorageKey());
                if (!java.nio.file.Files.isRegularFile(path)) {
                    throw new Failure("PDF blob not found in storage", 500);
                }
                pdfResource = new FileSystemResource(path);
            } else {
                pdfResource = new ByteArrayResource(job.pdf());
            }
            var form = new MultipartBodyBuilder();
            form.part("indexId", job.id().toString());
            form.part("source", job.filename());
            if (job.documentType() != null && !job.documentType().isBlank()) {
                form.part("documentType", job.documentType());
            }
            if (job.productName() != null && !job.productName().isBlank()) {
                form.part("productName", job.productName());
            }
            if (job.productVersion() != null && !job.productVersion().isBlank()) {
                form.part("productVersion", job.productVersion());
            }
            form.part("file", pdfResource, MediaType.APPLICATION_PDF).filename(job.filename());
            result = post(longRunningHttp, "/internal/index/pdf", form.build(), MediaType.MULTIPART_FORM_DATA);
        }
        if (result.path("chunksIndexed").asInt() < 1) {
            throw new Failure("Index service reported no indexed chunks", 422);
        }
    }

    JsonNode search(UUID indexId, String query, MetadataFilter filter) {
        var f = filter == null ? MetadataFilter.EMPTY : filter;
        return post(searchHttp, "/internal/search",
                new QueryRequest(indexId, query, f.documentTypes(), f.productName(), f.productVersion()));
    }

    JsonNode ask(UUID indexId, String query, MetadataFilter filter) {
        var f = filter == null ? MetadataFilter.EMPTY : filter;
        return post(longRunningHttp, "/internal/ask",
                new QueryRequest(indexId, query, f.documentTypes(), f.productName(), f.productVersion()));
    }

    JsonNode search(List<UUID> indexIds, String query, MetadataFilter filter) {
        var f = filter == null ? MetadataFilter.EMPTY : filter;
        return post(searchHttp, "/internal/search",
                new WorkspaceQueryRequest(indexIds, query, f.documentTypes(), f.productName(), f.productVersion()));
    }

    JsonNode ask(List<UUID> indexIds, String query, MetadataFilter filter) {
        var f = filter == null ? MetadataFilter.EMPTY : filter;
        return post(longRunningHttp, "/internal/ask",
                new WorkspaceQueryRequest(indexIds, query, f.documentTypes(), f.productName(), f.productVersion()));
    }

    void streamAsk(UUID indexId, String query, MetadataFilter filter, OutputStream out) throws IOException {
        var f = filter == null ? MetadataFilter.EMPTY : filter;
        streamPost("/internal/ask/stream",
                new QueryRequest(indexId, query, f.documentTypes(), f.productName(), f.productVersion()), out);
    }

    void streamAsk(List<UUID> indexIds, String query, MetadataFilter filter, OutputStream out) throws IOException {
        var f = filter == null ? MetadataFilter.EMPTY : filter;
        streamPost("/internal/ask/stream",
                new WorkspaceQueryRequest(indexIds, query, f.documentTypes(), f.productName(), f.productVersion()), out);
    }

    private void streamPost(String endpoint, Object payload, OutputStream out) throws IOException {
        byte[] body = mapper.writeValueAsBytes(payload);
        var request = HttpRequest.newBuilder(indexBaseUri.resolve(endpoint))
                .timeout(Duration.ofSeconds(85))
                .header("Authorization", bearerToken)
                .header("Content-Type", MediaType.APPLICATION_JSON_VALUE)
                .header("Accept", MediaType.TEXT_EVENT_STREAM_VALUE)
                .POST(HttpRequest.BodyPublishers.ofByteArray(body))
                .build();
        HttpResponse<InputStream> response;
        try {
            response = streamHttp.send(request, HttpResponse.BodyHandlers.ofInputStream());
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            throw new Failure("Index service stream interrupted", 502);
        }
        try (InputStream stream = response.body()) {
            if (response.statusCode() >= 400) {
                throw new Failure("Index service returned HTTP " + response.statusCode(), response.statusCode());
            }
            byte[] buffer = new byte[4096];
            int read;
            while ((read = stream.read(buffer)) != -1) {
                out.write(buffer, 0, read);
                out.flush();
            }
        }
    }

    void purge(UUID indexId) {
        post(searchHttp, "/internal/index/purge", new PurgeRequest(indexId));
    }

    private JsonNode post(RestClient http, String endpoint, Object body) {
        return post(http, endpoint, body, MediaType.APPLICATION_JSON);
    }

    private JsonNode post(RestClient http, String endpoint, Object body, MediaType contentType) {
        try {
            JsonNode result = http.post().uri(endpoint).contentType(contentType)
                    .body(body).retrieve().body(JsonNode.class);
            if (result == null || !result.isObject()) {
                throw new Failure("Index service returned an invalid response", 502);
            }
            return result;
        } catch (RestClientResponseException error) {
            int status = error.getStatusCode().value();
            throw new Failure("Index service returned HTTP " + status, status);
        } catch (RestClientException error) {
            throw new Failure("Index service request failed", 502);
        }
    }

    private record IndexRequest(UUID indexId, String source, String text,
                                String documentType, String productName, String productVersion) {
    }

    private record QueryRequest(UUID indexId, String query,
                                List<String> documentTypes, String productName, String productVersion) {
    }

    private record WorkspaceQueryRequest(List<UUID> indexIds, String query,
                                         List<String> documentTypes, String productName, String productVersion) {
    }

    private record PurgeRequest(UUID indexId) {
    }

    static final class Failure extends RuntimeException {
        private final int status;

        Failure(String message, int status) {
            super(message);
            this.status = status;
        }

        int status() {
            return status;
        }

        boolean retryable() {
            return status == 408 || status == 429 || status >= 500;
        }
    }
}
