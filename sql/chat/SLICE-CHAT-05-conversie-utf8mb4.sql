-- SLICE-CHAT-05: revenire la codarea initiala, pentru suport emoji.
-- Executare MANUALA de operator pe serverul K-BOT, in AVACONT_COMUN.
-- CONVERT modifica si coloanele text existente, nu doar default-ul tabelei.
-- Legaturile dintre tabele folosesc chei numerice; nu dezactivam FOREIGN_KEY_CHECKS.

ALTER TABLE AVACONT_COMUN.CHAT_IDENTITATI
    CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

ALTER TABLE AVACONT_COMUN.CHAT_CONVERSATII
    CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

ALTER TABLE AVACONT_COMUN.CHAT_PARTICIPANTI
    CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

ALTER TABLE AVACONT_COMUN.CHAT_MESAJE
    CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CONVERT poate largi automat TEXT si converteste inclusiv coloana ASCII.
-- Restabilim definitiile explicite din SLICE-CHAT-01-schema.sql.
ALTER TABLE AVACONT_COMUN.CHAT_MESAJE
    MODIFY COLUMN CONTINUT TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
    MODIFY COLUMN CHEIE_CLIENT CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL;

ALTER TABLE AVACONT_COMUN.CHAT_CITIRI
    CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
