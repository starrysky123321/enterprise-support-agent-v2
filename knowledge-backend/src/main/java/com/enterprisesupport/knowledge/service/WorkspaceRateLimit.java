package com.enterprisesupport.knowledge.service;

import com.enterprisesupport.knowledge.config.KnowledgeProperties;

import io.micrometer.core.instrument.MeterRegistry;
import java.time.Duration;
import java.util.List;
import org.springframework.core.io.ClassPathResource;
import org.springframework.dao.DataAccessException;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;

@Component
public final class WorkspaceRateLimit {
    private static final long REDIS_COOLDOWN = Duration.ofSeconds(1).toNanos();
    private static final KnowledgeProperties.Budget LOGIN_BUDGET = new KnowledgeProperties.Budget(12, 8);

    private final KnowledgeProperties.RateLimit config;
    private final StringRedisTemplate redis;
    private final MeterRegistry metrics;
    private final DefaultRedisScript<Long> bucket;
    private volatile long probeAfter = Long.MIN_VALUE;

    public WorkspaceRateLimit(KnowledgeProperties config, StringRedisTemplate redis, MeterRegistry metrics) {
        this.config = config.rateLimit();
        this.redis = redis;
        this.metrics = metrics;
        bucket = new DefaultRedisScript<>();
        bucket.setLocation(new ClassPathResource("redis/token_bucket.lua"));
        bucket.setResultType(Long.class);
    }

    public enum Operation {
        SEARCH("search"), ASK("ask"), LOGIN("login");

        final String label;

        Operation(String label) {
            this.label = label;
        }
    }

    public void acquire(String workspace, Operation operation) {
        if (!config.enabled()) {
            return;
        }
        if (System.nanoTime() < probeAfter) {
            onRedisUnavailable(operation);
            return;
        }
        var budget = switch (operation) {
            case SEARCH -> config.search();
            case ASK -> config.ask();
            case LOGIN -> LOGIN_BUDGET;
        };
        long delay;
        try {
            String key = "knowledge:quota:" + workspace + ":" + operation.label;
            Long result = redis.execute(bucket, List.of(key),
                    Integer.toString(budget.perMinute()), Integer.toString(budget.burst()));
            if (result == null) {
                throw new DataAccessResourceFailureException("Quota script did not return a decision");
            }
            delay = result;
        } catch (DataAccessException error) {
            probeAfter = System.nanoTime() + REDIS_COOLDOWN;
            onRedisUnavailable(operation);
            return;
        }
        if (delay > 0) {
            count(operation, "throttled");
            throw new Rejected(HttpStatus.TOO_MANY_REQUESTS, (delay + 999) / 1000,
                    operation == Operation.LOGIN ? "Account login quota reached" : "Workspace request quota reached");
        }
    }

    private void onRedisUnavailable(Operation operation) {
        if (operation != Operation.SEARCH) {
            count(operation, "unavailable");
            throw new Rejected(HttpStatus.SERVICE_UNAVAILABLE, 1,
                    operation == Operation.LOGIN ? "Account login quota unavailable" : "Workspace quota service unavailable");
        }
        count(operation, "local_fallback");
    }

    private void count(Operation operation, String outcome) {
        metrics.counter("knowledge.quota.decisions", "operation", operation.label, "outcome", outcome).increment();
    }

    public static final class Rejected extends RuntimeException {
        public final HttpStatus status;
        public final long retryAfter;

        Rejected(HttpStatus status, long retryAfter, String message) {
            super(message);
            this.status = status;
            this.retryAfter = retryAfter;
        }
    }
}
