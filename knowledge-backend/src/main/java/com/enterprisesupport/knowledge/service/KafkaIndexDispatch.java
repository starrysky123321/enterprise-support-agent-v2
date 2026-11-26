package com.enterprisesupport.knowledge.service;

import com.enterprisesupport.knowledge.config.KnowledgeProperties;
import com.enterprisesupport.knowledge.repository.IndexOutbox;

import io.micrometer.core.instrument.MeterRegistry;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import org.apache.kafka.clients.admin.NewTopic;
import org.apache.kafka.common.TopicPartition;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.kafka.KafkaException;
import org.springframework.kafka.config.TopicBuilder;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.listener.DeadLetterPublishingRecoverer;
import org.springframework.kafka.listener.DefaultErrorHandler;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.util.backoff.FixedBackOff;

@Component
@ConditionalOnProperty(name = "knowledge.kafka-enabled", havingValue = "true")
public final class KafkaIndexDispatch {
    public static final String TOPIC = "knowledge.index.requested.v1";
    public static final String DEAD_LETTER_TOPIC = TOPIC + ".DLT";
    public static final String GROUP = "knowledge-index-workers-v1";

    private final IndexOutbox outbox;
    private final KafkaTemplate<String, String> kafka;
    private final MeterRegistry metrics;

    public KafkaIndexDispatch(IndexOutbox outbox, KafkaTemplate<String, String> kafka, MeterRegistry metrics) {
        this.outbox = outbox;
        this.kafka = kafka;
        this.metrics = metrics;
    }

    @Scheduled(fixedDelayString = "${knowledge.outbox-poll-ms:150}")
    public void relay() {
        boolean unavailable = false;
        for (var message : outbox.claimBatch()) {
            if (!unavailable) {
                String job = message.jobId().toString();
                try {
                    kafka.send(TOPIC, job, job).get(5, TimeUnit.SECONDS);
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    return;
                } catch (KafkaException | ExecutionException | TimeoutException failure) {
                    unavailable = true;
                }
            }
            if (unavailable) {
                outbox.defer(message);
            } else {
                outbox.acknowledge(message);
            }
            metrics.counter("knowledge.index.dispatch", "result", unavailable ? "deferred" : "sent").increment();
        }
    }

    @Bean
    public static NewTopic indexRequests(KnowledgeProperties config) {
        return topic(TOPIC, config);
    }

    @Bean
    public static NewTopic indexDeadLetters(KnowledgeProperties config) {
        return topic(DEAD_LETTER_TOPIC, config);
    }

    private static NewTopic topic(String name, KnowledgeProperties config) {
        return TopicBuilder.name(name).partitions(config.kafkaPartitions()).replicas(config.kafkaReplicas()).build();
    }

    @Bean
    public static DefaultErrorHandler indexEventErrorHandler(KafkaTemplate<String, String> kafka) {
        var recoverer = new DeadLetterPublishingRecoverer(kafka,
                (record, error) -> new TopicPartition(DEAD_LETTER_TOPIC, record.partition()));
        recoverer.setFailIfSendResultIsError(true);
        var handler = new DefaultErrorHandler(recoverer, new FixedBackOff(1000, 2));
        handler.addNotRetryableExceptions(IllegalArgumentException.class);
        return handler;
    }
}
