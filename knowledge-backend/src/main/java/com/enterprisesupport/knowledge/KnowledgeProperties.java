package com.enterprisesupport.knowledge;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.net.URI;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

@ConfigurationProperties("knowledge")
@Validated
record KnowledgeProperties(
        @NotBlank @Size(min = 32) String apiToken,
        @NotBlank @Size(min = 32) String indexToken,
        @NotNull URI indexUrl,
        @Min(1) @Max(64) int workerConcurrency,
        @Min(1) @Max(10) int maxAttempts,
        boolean kafkaEnabled,
        @Min(1) int kafkaPartitions,
        @Min(1) short kafkaReplicas,
        @Min(1) @Max(256) int searchConcurrency,
        @Min(1) @Max(64) int askConcurrency,
        @Min(1) @Max(32) int pdfUploadConcurrency,
        @NotNull @Valid PdfStorage pdfStorage,
        @NotNull @Valid SearchCache searchCache,
        @NotNull @Valid RateLimit rateLimit) {

    record PdfStorage(
            @NotBlank @Pattern(regexp = "filesystem|database") String mode,
            @NotBlank String directory) {
        boolean isFilesystem() {
            return "filesystem".equals(mode);
        }
    }

    record SearchCache(boolean enabled,
                       @Min(1) @Max(300) int ttlSeconds,
                       @Min(1) @Max(4096) int maxEntries,
                       @NotBlank @Pattern(regexp = "[a-zA-Z0-9._-]{1,64}") String revision) {
    }

    record RateLimit(boolean enabled, @NotNull @Valid Budget search, @NotNull @Valid Budget ask) {
    }

    record Budget(@Min(1) @Max(6_000_000) int perMinute, @Min(1) @Max(100_000) int burst) {
    }
}
