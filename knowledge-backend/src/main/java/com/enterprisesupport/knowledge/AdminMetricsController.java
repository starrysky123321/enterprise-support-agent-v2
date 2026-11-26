package com.enterprisesupport.knowledge;

import static com.enterprisesupport.knowledge.AccessGrant.ATTRIBUTE;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.dao.DataAccessException;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@ConditionalOnProperty(name = "knowledge.api-enabled", havingValue = "true", matchIfMissing = true)
@RequestMapping("/api/knowledge/admin")
final class AdminMetricsController {
    private final MeterRegistry metrics;
    private final StringRedisTemplate redis;

    AdminMetricsController(MeterRegistry metrics, StringRedisTemplate redis) {
        this.metrics = metrics;
        this.redis = redis;
    }

    @GetMapping("/metrics")
    Map<String, Object> summary(@RequestAttribute(ATTRIBUTE) AccessGrant grant) {
        grant.requireAdmin();
        long cacheHit = counter("knowledge.search.cache", "outcome", "hit");
        long cacheMiss = counter("knowledge.search.cache", "outcome", "miss");
        try {
            var entries = redis.opsForHash().entries(SearchResultCache.METRICS_KEY);
            if (entries != null && !entries.isEmpty()) {
                cacheHit = Math.max(cacheHit, parseLong(entries.get("hit")));
                cacheMiss = Math.max(cacheMiss, parseLong(entries.get("miss")));
            }
        } catch (DataAccessException ignored) {
            // Fall back to local instance Micrometer counters when Redis is unavailable.
        }
        var cache = Map.of(
                "hit", cacheHit,
                "miss", cacheMiss,
                "unavailable", counter("knowledge.search.cache", "outcome", "unavailable"));
        var quota = Map.of(
                "searchThrottled", counter("knowledge.quota.decisions", "operation", "search", "outcome", "throttled"),
                "askThrottled", counter("knowledge.quota.decisions", "operation", "ask", "outcome", "throttled"),
                "searchFallback", counter("knowledge.quota.decisions", "operation", "search", "outcome", "local_fallback"),
                "askUnavailable", counter("knowledge.quota.decisions", "operation", "ask", "outcome", "unavailable"));
        var jobs = Map.of(
                "indexed", counter("knowledge.index.jobs", "result", "indexed"),
                "retry", counter("knowledge.index.jobs", "result", "retry"),
                "failed", counter("knowledge.index.jobs", "result", "failed"),
                "leaseExpired", counter("knowledge.index.jobs", "result", "lease_expired"));
        var dbPool = Map.of(
                "active", gauge("hikaricp.connections.active"),
                "idle", gauge("hikaricp.connections.idle"),
                "pending", gauge("hikaricp.connections.pending"));
        var jvm = Map.of(
                "heapUsedMb", Math.round(sumGauge("jvm.memory.used", "area", "heap") / (1024.0 * 1024.0)),
                "heapMaxMb", Math.round(sumGauge("jvm.memory.max", "area", "heap") / (1024.0 * 1024.0)));

        var body = new LinkedHashMap<String, Object>();
        body.put("cache", cache);
        body.put("quota", quota);
        body.put("jobs", jobs);
        body.put("dbPool", dbPool);
        body.put("jvm", jvm);
        return body;
    }

    private long counter(String name, String... tags) {
        Counter found = metrics.find(name).tags(tags).counter();
        return found == null ? 0L : Math.round(found.count());
    }

    private long gauge(String name) {
        Gauge found = metrics.find(name).gauge();
        return found == null || Double.isNaN(found.value()) ? 0L : Math.round(found.value());
    }

    private static long parseLong(Object raw) {
        if (raw == null) {
            return 0L;
        }
        try {
            return Long.parseLong(raw.toString());
        } catch (NumberFormatException ignored) {
            return 0L;
        }
    }

    private double sumGauge(String name, String... tags) {
        return metrics.find(name).tags(tags).gauges().stream()
                .mapToDouble(Gauge::value)
                .filter(v -> !Double.isNaN(v) && v > 0)
                .sum();
    }
}
