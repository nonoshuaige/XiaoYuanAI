ALTER TABLE chat_events
    MODIFY COLUMN payload_json LONGTEXT NOT NULL DEFAULT ('{}');

ALTER TABLE model_call_audits
    MODIFY COLUMN provider_responses_json LONGTEXT NOT NULL DEFAULT ('[]');
