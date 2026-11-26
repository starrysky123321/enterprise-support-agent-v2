package com.enterprisesupport.knowledge.service;

import com.enterprisesupport.knowledge.config.KnowledgeProperties;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Arrays;
import java.util.HexFormat;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ResponseStatusException;

@Component
public final class PdfBlobStore {
    private static final Logger LOGGER = LoggerFactory.getLogger(PdfBlobStore.class);
    private static final byte[] PDF_SIGNATURE = "%PDF-".getBytes(StandardCharsets.US_ASCII);
    private static final int BUFFER_SIZE = 64 * 1024;

    private final boolean filesystem;
    private final Path root;
    private final Path stagingDir;

    public PdfBlobStore(KnowledgeProperties properties) throws IOException {
        var config = properties.pdfStorage();
        this.filesystem = config.isFilesystem();
        this.root = Path.of(config.directory()).toAbsolutePath().normalize();
        this.stagingDir = root.resolve(".staging");
        if (filesystem) {
            Files.createDirectories(stagingDir);
        }
    }

    public boolean usesExternalStorage() {
        return filesystem;
    }

    public StagedPdf stage(InputStream input) throws IOException {
        byte[] header = input.readNBytes(PDF_SIGNATURE.length);
        if (!Arrays.equals(PDF_SIGNATURE, header)) {
            throw new ResponseStatusException(HttpStatus.UNSUPPORTED_MEDIA_TYPE, "Expected a PDF file");
        }
        MessageDigest sha256 = sha256();
        sha256.update(header);

        if (filesystem) {
            Path temp = Files.createTempFile(stagingDir, "upload-", ".pdf.tmp");
            try (OutputStream out = Files.newOutputStream(temp)) {
                out.write(header);
                copyAndDigest(input, out, sha256);
            } catch (IOException | RuntimeException error) {
                deleteQuietly(temp);
                throw error;
            }
            return new StagedPdf(HexFormat.of().formatHex(sha256.digest()), temp, null);
        }

        var buffer = new ByteArrayOutputStream();
        buffer.write(header);
        copyAndDigest(input, buffer, sha256);
        return new StagedPdf(HexFormat.of().formatHex(sha256.digest()), null, buffer.toByteArray());
    }

    public String commit(UUID versionId, StagedPdf staged) throws IOException {
        if (!filesystem || staged.tempPath == null) {
            throw new IllegalStateException("External PDF storage is not enabled");
        }
        String id = versionId.toString();
        String key = id.substring(0, 2) + "/" + id.substring(2, 4) + "/" + id + ".pdf";
        Path target = resolve(key);
        Files.createDirectories(target.getParent());
        try {
            Files.move(staged.tempPath, target, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        } catch (AtomicMoveNotSupportedException unsupported) {
            Files.move(staged.tempPath, target, StandardCopyOption.REPLACE_EXISTING);
        }
        staged.committed = true;
        return key;
    }

    public Path resolve(String storageKey) {
        if (storageKey == null || storageKey.isBlank()) {
            throw new IllegalArgumentException("PDF storage key must not be blank");
        }
        Path target = root.resolve(storageKey).normalize();
        if (!target.startsWith(root) || target.equals(root)) {
            throw new IllegalArgumentException("Invalid PDF storage key");
        }
        return target;
    }

    public void delete(String storageKey) throws IOException {
        if (storageKey == null || storageKey.isBlank()) {
            return;
        }
        Files.deleteIfExists(resolve(storageKey));
    }

    public void deleteQuietly(String storageKey) {
        try {
            delete(storageKey);
        } catch (IOException | RuntimeException error) {
            LOGGER.warn("Failed to remove PDF blob {}: {}", storageKey, error.getMessage());
        }
    }

    private static void deleteQuietly(Path path) {
        if (path == null) {
            return;
        }
        try {
            Files.deleteIfExists(path);
        } catch (IOException error) {
            LOGGER.warn("Failed to remove staged PDF {}: {}", path.getFileName(), error.getMessage());
        }
    }

    private static void copyAndDigest(InputStream input, OutputStream out, MessageDigest digest) throws IOException {
        byte[] chunk = new byte[BUFFER_SIZE];
        int read;
        while ((read = input.read(chunk)) != -1) {
            digest.update(chunk, 0, read);
            out.write(chunk, 0, read);
        }
    }

    private static MessageDigest sha256() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException(impossible);
        }
    }

    public static final class StagedPdf implements AutoCloseable {
        private final String sha256;
        private final Path tempPath;
        private final byte[] bytes;
        private boolean committed;

        private StagedPdf(String sha256, Path tempPath, byte[] bytes) {
            this.sha256 = sha256;
            this.tempPath = tempPath;
            this.bytes = bytes;
        }

        public String sha256() {
            return sha256;
        }

        public byte[] bytes() {
            return bytes;
        }

        @Override
        public void close() {
            if (!committed) {
                deleteQuietly(tempPath);
            }
        }
    }
}
