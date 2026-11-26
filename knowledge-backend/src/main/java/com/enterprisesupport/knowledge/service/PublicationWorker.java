package com.enterprisesupport.knowledge.service;

import com.enterprisesupport.knowledge.config.KnowledgeProperties;
import com.enterprisesupport.knowledge.repository.VersionCatalog;

import io.micrometer.core.instrument.MeterRegistry;
import jakarta.annotation.PreDestroy;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Semaphore;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.TimeUnit;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
@ConditionalOnProperty(name = "knowledge.worker-enabled", havingValue = "true", matchIfMissing = true)
public final class PublicationWorker {
    private static final Logger LOGGER = LoggerFactory.getLogger(PublicationWorker.class);
    private final VersionCatalog catalog;
    private final IndexClient index;
    private final PdfBlobStore blobStore;
    private final KnowledgeProperties properties;
    private final MeterRegistry metrics;
    private final ExecutorService workers;
    private final Semaphore slots;
    private volatile boolean stopping;

    public PublicationWorker(VersionCatalog catalog, IndexClient index, PdfBlobStore blobStore,
                      KnowledgeProperties properties, MeterRegistry metrics) {
        this.catalog = catalog;
        this.index = index;
        this.blobStore = blobStore;
        this.properties = properties;
        this.metrics = metrics;
        workers = Executors.newFixedThreadPool(properties.workerConcurrency());
        slots = new Semaphore(properties.workerConcurrency());
    }

    @Scheduled(fixedDelayString = "${knowledge.poll-delay-ms:500}")
    public void poll() {
        int expired = catalog.failExpired(properties.maxAttempts());
        if (expired > 0) {
            metrics.counter("knowledge.index.jobs", "result", "lease_expired").increment(expired);
        }
        if (properties.kafkaEnabled()) {
            catalog.reconcileDispatch(properties.maxAttempts());
            return;
        }
        for (int slot = 0; !stopping && slot < properties.workerConcurrency() && slots.tryAcquire(); slot++) {
            boolean submitted = false;
            try {
                var job = catalog.claimNext(properties.maxAttempts());
                if (job.isEmpty()) {
                    return;
                }
                workers.execute(() -> drain(job.get()));
                submitted = true;
            } finally {
                if (!submitted) {
                    slots.release();
                }
            }
        }
    }

    @KafkaListener(topics = KafkaIndexDispatch.TOPIC, groupId = KafkaIndexDispatch.GROUP,
            autoStartup = "${knowledge.kafka-enabled:false}", concurrency = "${knowledge.worker-concurrency:4}")
    public void consume(String jobId) {
        var job = catalog.claimDispatched(UUID.fromString(jobId));
        if (job.isPresent()) {
            process(job.get());
        } else {
            metrics.counter("knowledge.index.dispatch", "result", "skipped").increment();
        }
    }

    @Scheduled(fixedDelayString = "${knowledge.purge-poll-ms:500}")
    public void purgeRetiredVersion() {
        catalog.claimPurge().ifPresent(job -> {
            try {
                index.purge(job.id());
                blobStore.delete(job.pdfStorageKey());
                catalog.completePurge(job);
                metrics.counter("knowledge.index.purge", "result", "completed").increment();
            } catch (IndexClient.Failure | java.io.IOException error) {
                metrics.counter("knowledge.index.purge", "result", "retry").increment();
                LOGGER.warn("Purge of retired index {} will retry after its lease: {}", job.id(), error.getMessage());
            }
        });
    }

    private void drain(VersionCatalog.IndexJob first) {
        var job = first;
        try {
            while (job != null) {
                process(job);
                job = stopping ? null : catalog.claimNext(properties.maxAttempts()).orElse(null);
            }
        } catch (RuntimeException failure) {
            LOGGER.error("Index worker stopped. Active jobs remain recoverable by lease", failure);
        } finally {
            slots.release();
        }
    }

    private void process(VersionCatalog.IndexJob job) {
        try {
            index.index(job);
        } catch (IndexClient.Failure failure) {
            int delay = 0;
            if (failure.retryable() && job.cycleAttempt() < properties.maxAttempts()) {
                delay = Math.min(30, 1 << (job.cycleAttempt() - 1)) + ThreadLocalRandom.current().nextInt(2);
            }
            if (catalog.fail(job, failure.getMessage(), delay)) {
                metrics.counter("knowledge.index.jobs", "result", delay > 0 ? "retry" : "failed").increment();
            }
            return;
        }
        if (catalog.publish(job)) {
            metrics.counter("knowledge.index.jobs", "result", "indexed").increment();
        }
    }

    @PreDestroy
    public void shutdown() throws InterruptedException {
        stopping = true;
        workers.shutdown();
        if (!workers.awaitTermination(65, TimeUnit.SECONDS)) {
            workers.shutdownNow();
        }
    }
}
