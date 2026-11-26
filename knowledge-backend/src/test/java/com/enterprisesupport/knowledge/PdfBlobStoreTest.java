package com.enterprisesupport.knowledge;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayInputStream;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.web.server.ResponseStatusException;

class PdfBlobStoreTest {

    private static KnowledgeProperties properties(String mode, Path dir) {
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
                new KnowledgeProperties.PdfStorage(mode, dir.toString()),
                new KnowledgeProperties.SearchCache(false, 30, 512, "default"),
                new KnowledgeProperties.RateLimit(
                        false,
                        new KnowledgeProperties.Budget(60000, 1000),
                        new KnowledgeProperties.Budget(7200, 120)));
    }

    @Test
    void stagesCommitsResolvesAndDeletesExternalPdfBlob(@TempDir Path tempDir) throws Exception {
        var store = new PdfBlobStore(properties("filesystem", tempDir));
        assertTrue(store.usesExternalStorage());
        byte[] sample = "%PDF-1.4\nhello world\n%%EOF\n".getBytes(StandardCharsets.US_ASCII);
        UUID versionId = UUID.randomUUID();

        String key;
        try (var staged = store.stage(new ByteArrayInputStream(sample))) {
            assertEquals(TokenDigest.sha256(sample), staged.sha256());
            key = store.commit(versionId, staged);
        }

        Path stored = store.resolve(key);
        assertTrue(Files.isRegularFile(stored));
        assertArrayEquals(sample, Files.readAllBytes(stored));

        store.delete(key);
        assertFalse(Files.exists(stored));
    }

    @Test
    void rejectsNonPdfSignatureAndPathTraversal(@TempDir Path tempDir) throws Exception {
        var store = new PdfBlobStore(properties("filesystem", tempDir));
        assertThrows(ResponseStatusException.class,
                () -> store.stage(new ByteArrayInputStream("not a pdf".getBytes(StandardCharsets.UTF_8))));
        assertThrows(IllegalArgumentException.class, () -> store.resolve("../escape.pdf"));
    }

    @Test
    void databaseModeBuffersBytesWithoutExternalFiles(@TempDir Path tempDir) throws Exception {
        var store = new PdfBlobStore(properties("database", tempDir));
        assertFalse(store.usesExternalStorage());
        byte[] sample = "%PDF-1.7\nbody".getBytes(StandardCharsets.US_ASCII);
        try (var staged = store.stage(new ByteArrayInputStream(sample))) {
            assertNotNull(staged.bytes());
            assertArrayEquals(sample, staged.bytes());
        }
    }
}
