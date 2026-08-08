ALTER TABLE conversation_rounds
    ADD COLUMN model_step_usage_json LONGTEXT NOT NULL DEFAULT ('[]')
    AFTER token_usage_estimated;
