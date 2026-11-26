package com.enterprisesupport.knowledge.service;

import com.enterprisesupport.knowledge.config.KnowledgeProperties;
import com.enterprisesupport.knowledge.security.TokenDigest;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.MeterRegistry;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.UUID;
import java.util.function.Supplier;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.core.io.ClassPathResource;
import org.springframework.dao.DataAccessException;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;

@Component
@ConditionalOnProperty(name = "knowledge.api-enabled", havingValue = "true", matchIfMissing = true)
public final class SearchResultCache {
    public static final String METRICS_KEY = "knowledge:metrics:search_cache";
    private static final String PREFIX = "knowledge:cache:{search}:v1:";
    private static final String EXPIRATIONS = PREFIX + "expirations";
    private static final int MAX_VALUE_BYTES = 64 * 1024;
    private static final long RETRY_DELAY = Duration.ofSeconds(1).toNanos();

    private final StringRedisTemplate redis;
    private final ObjectMapper mapper;
    private final MeterRegistry metrics;
    private final KnowledgeProperties.SearchCache config;
    private final String ttlMillis;
    private final String maxEntries;
    private final DefaultRedisScript<Long> store;
    private volatile long probeAfter = Long.MIN_VALUE;

    public SearchResultCache(StringRedisTemplate redis, ObjectMapper mapper, MeterRegistry metrics,
                      KnowledgeProperties properties) {
        this.redis = redis;
        this.mapper = mapper;
        this.metrics = metrics;
        this.config = properties.searchCache();
        this.ttlMillis = Long.toString(Duration.ofSeconds(config.ttlSeconds()).toMillis());
        this.maxEntries = Integer.toString(config.maxEntries());
        this.store = new DefaultRedisScript<>();
        store.setLocation(new ClassPathResource("redis/store_search_result.lua"));
        store.setResultType(Long.class);
    }

    public JsonNode search(String workspace, UUID publishedId, String query, Supplier<JsonNode> retrieve) {
        return search(workspace, publishedId, query, "", retrieve);
    }

    public JsonNode search(String workspace, UUID publishedId, String query, String filterFingerprint,
                    Supplier<JsonNode> retrieve) {
        return lookup(singleDocumentKey(workspace, publishedId, query, filterFingerprint), retrieve);
    }

    public JsonNode searchWorkspace(String workspace, long catalogVersion, List<UUID> publishedIds, String query,
                             Supplier<JsonNode> retrieve) {
        return searchWorkspace(workspace, catalogVersion, publishedIds, query, "", retrieve);
    }

    public JsonNode searchWorkspace(String workspace, long catalogVersion, List<UUID> publishedIds, String query,
                             String filterFingerprint, Supplier<JsonNode> retrieve) {
        if (publishedIds.isEmpty()) {
            return retrieve.get();
        }
        String key = publishedIds.size() == 1
                ? singleDocumentKey(workspace, publishedIds.get(0), query, filterFingerprint)
                : workspaceKey(workspace, catalogVersion, query, filterFingerprint);
        return lookup(key, retrieve);
    }

    private JsonNode lookup(String key, Supplier<JsonNode> retrieve) {
        if (!config.enabled()) {
            return retrieve.get();
        }
        if (System.nanoTime() < probeAfter) {
            count("unavailable");
            return retrieve.get();
        }
        JsonNode cached = read(key);
        if (cached != null) {
            count("hit");
            return cached;
        }
        JsonNode result = retrieve.get();
        if (System.nanoTime() >= probeAfter && result.path("chunks").isArray()) {
            write(key, result);
        }
        return result;
    }

    private String singleDocumentKey(String workspace, UUID publishedId, String query, String filterFingerprint) {
        String suffix = (filterFingerprint == null || filterFingerprint.isEmpty()) ? "" : "\nf:" + filterFingerprint;
        String fingerprint = config.revision() + '\n' + workspace + "\ndoc:" + publishedId + suffix + '\n' + query;
        return PREFIX + "entry:" + TokenDigest.sha256(fingerprint);
    }

    private String workspaceKey(String workspace, long catalogVersion, String query, String filterFingerprint) {
        // Workspace catalog_version advances atomically on every publication or deletion.
        String suffix = (filterFingerprint == null || filterFingerprint.isEmpty()) ? "" : "\nf:" + filterFingerprint;
        String fingerprint = config.revision() + '\n' + workspace + "\ncv:" + catalogVersion + suffix + '\n' + query;
        return PREFIX + "entry:" + TokenDigest.sha256(fingerprint);
    }

    private JsonNode read(String key) {
        try {
            String value = redis.opsForValue().get(key);
            if (value != null) {
                JsonNode result = mapper.readTree(value);
                if (result != null && result.path("chunks").isArray()) {
                    return result;
                }
            }
            count("miss");
        } catch (JsonProcessingException error) {
            count("miss");
        } catch (DataAccessException error) {
            unavailable();
        }
        return null;
    }

    private void write(String key, JsonNode result) {
        String json = result.toString();
        if (json.getBytes(StandardCharsets.UTF_8).length > MAX_VALUE_BYTES) {
            return;
        }
        try {
            redis.execute(store, List.of(key, EXPIRATIONS), json, ttlMillis, maxEntries);
        } catch (DataAccessException error) {
            unavailable();
        }
    }

    private void unavailable() {
        probeAfter = System.nanoTime() + RETRY_DELAY;
        count("unavailable");
    }

    private void count(String outcome) {
        metrics.counter("knowledge.search.cache", "outcome", outcome).increment();
        if ("hit".equals(outcome) || "miss".equals(outcome)) {
            try {
                var ops = redis.opsForHash();
                if (ops != null) {
                    ops.increment(METRICS_KEY, outcome, 1L);
                }
            } catch (DataAccessException ignored) {
                // Best-effort cluster counter; local Micrometer counter is already updated.
            }
        }
    }
}
