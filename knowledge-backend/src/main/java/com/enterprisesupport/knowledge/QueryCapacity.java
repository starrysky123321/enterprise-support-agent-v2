package com.enterprisesupport.knowledge;

import io.micrometer.core.instrument.MeterRegistry;
import java.util.concurrent.Semaphore;
import java.util.function.Supplier;
import org.springframework.stereotype.Component;

@Component
final class QueryCapacity {
    private final Semaphore searches;
    private final Semaphore answers;
    private final MeterRegistry metrics;
    private final WorkspaceRateLimit rateLimit;

    QueryCapacity(KnowledgeProperties properties, MeterRegistry metrics, WorkspaceRateLimit rateLimit) {
        searches = new Semaphore(properties.searchConcurrency());
        answers = new Semaphore(properties.askConcurrency());
        this.metrics = metrics;
        this.rateLimit = rateLimit;
    }

    <T> T search(String workspace, Supplier<T> task) {
        return run(searches, workspace, WorkspaceRateLimit.Operation.SEARCH, task);
    }

    <T> T answer(String workspace, Supplier<T> task) {
        return run(answers, workspace, WorkspaceRateLimit.Operation.ASK, task);
    }

    private <T> T run(Semaphore slots, String workspace, WorkspaceRateLimit.Operation operation, Supplier<T> task) {
        if (!slots.tryAcquire()) {
            metrics.counter("knowledge.capacity.rejected", "operation", operation.label).increment();
            throw new IndexClient.Failure("Request capacity reached", 503);
        }
        try {
            rateLimit.acquire(workspace, operation);
            return task.get();
        } finally {
            slots.release();
        }
    }
}
