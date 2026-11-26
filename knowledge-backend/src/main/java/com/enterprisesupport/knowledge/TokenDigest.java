package com.enterprisesupport.knowledge;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.HexFormat;

final class TokenDigest {
    private static final SecureRandom RANDOM = new SecureRandom();

    private TokenDigest() {
    }

    static String sha256(String value) {
        return sha256(value.getBytes(StandardCharsets.UTF_8));
    }

    static String sha256(byte[] value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(value);
            return HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("SHA-256 is unavailable", error);
        }
    }

    static String issue(String prefix) {
        byte[] secret = new byte[32];
        RANDOM.nextBytes(secret);
        return prefix + Base64.getUrlEncoder().withoutPadding().encodeToString(secret);
    }
}
