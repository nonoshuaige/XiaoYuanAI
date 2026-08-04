ALTER TABLE conversation_rounds
    ADD COLUMN context_window_tokens INT NOT NULL DEFAULT 16384 AFTER model_id,
    ADD COLUMN context_estimated_tokens INT NULL AFTER context_window_tokens,
    ADD COLUMN context_truncated TINYINT(1) NOT NULL DEFAULT 0 AFTER context_estimated_tokens,
    ADD COLUMN context_dropped_rounds INT NOT NULL DEFAULT 0 AFTER context_truncated,
    ADD COLUMN input_tokens BIGINT NULL AFTER context_dropped_rounds,
    ADD COLUMN output_tokens BIGINT NULL AFTER input_tokens,
    ADD COLUMN total_tokens BIGINT NULL AFTER output_tokens;
