ALTER TABLE conversation_rounds
    ADD COLUMN token_usage_estimated TINYINT(1) NOT NULL DEFAULT 0
    AFTER total_tokens;
