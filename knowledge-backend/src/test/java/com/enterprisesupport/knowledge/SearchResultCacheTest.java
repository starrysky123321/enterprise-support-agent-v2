package com.enterprisesupport.knowledge;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import java.net.URI;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;
import org.springframework.data.redis.core.script.RedisScript;

class SearchResultCacheTest {

    private static KnowledgeProperties properties() {
        return new KnowledgeProperties(
                "a".repeat(32),
                "b".repeat(32),
                URI.create("http://127.0.0.1:8011"),
                4,
                3,
                false,
                12,
                (short) 1,
                32,
                6,
                4,
                new KnowledgeProperties.PdfStorage("database", "./data/blobs"),
                new KnowledgeProperties.SearchCache(true, 30, 512, "rev1"),
                new KnowledgeProperties.RateLimit(
                        false,
                        new KnowledgeProperties.Budget(60000, 1000),
                        new KnowledgeProperties.Budget(7200, 120)));
    }

    @Test
    @SuppressWarnings("unchecked")
    void sharesSingleDocumentKeyAndInvalidatesMultiDocumentWorkspaceByCatalogVersion() {
        Map<String, String> backing = new ConcurrentHashMap<>();
        var redis = mock(StringRedisTemplate.class);
        var values = mock(ValueOperations.class);
        when(redis.opsForValue()).thenReturn(values);
        when(values.get(anyString())).thenAnswer(inv -> backing.get(inv.getArgument(0)));
        when(redis.execute(any(RedisScript.class), anyList(), any(), any(), any())).thenAnswer(inv -> {
            List<String> keys = inv.getArgument(1);
            String payload = inv.getArgument(2);
            backing.put(keys.get(0), payload);
            return 1L;
        });

        var mapper = new ObjectMapper();
        var metrics = new SimpleMeterRegistry();
        var cache = new SearchResultCache(redis, mapper, metrics, properties());
        var backendCalls = new AtomicInteger();
        var doc1 = UUID.randomUUID();
        var doc2 = UUID.randomUUID();

        var first = cache.searchWorkspace("ops", 1L, List.of(doc1), "timeout", () -> {
            backendCalls.incrementAndGet();
            return mapper.createObjectNode().set("chunks", mapper.createArrayNode());
        });
        assertEquals(1, backendCalls.get());

        // Single-document query shares the exact same cache key when workspace has 1 document.
        var shared = cache.search("ops", doc1, "timeout", () -> {
            backendCalls.incrementAndGet();
            return mapper.createObjectNode().set("chunks", mapper.createArrayNode());
        });
        assertEquals(first, shared);
        assertEquals(1, backendCalls.get());

        // Multi-document workspace search uses O(1) catalogVersion key.
        cache.searchWorkspace("ops", 2L, List.of(doc1, doc2), "timeout", () -> {
            backendCalls.incrementAndGet();
            return mapper.createObjectNode().set("chunks", mapper.createArrayNode());
        });
        cache.searchWorkspace("ops", 2L, List.of(doc1, doc2), "timeout", () -> {
            backendCalls.incrementAndGet();
            return mapper.createObjectNode().set("chunks", mapper.createArrayNode());
        });
        assertEquals(2, backendCalls.get());

        // Advancing catalogVersion invalidates immediately without deleting keys.
        cache.searchWorkspace("ops", 3L, List.of(doc1, doc2), "timeout", () -> {
            backendCalls.incrementAndGet();
            return mapper.createObjectNode().set("chunks", mapper.createArrayNode());
        });
        assertEquals(3, backendCalls.get());
    }
}
