SET @drop_quick_replies = (
    SELECT IF(
        EXISTS(
            SELECT 1
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
                AND TABLE_NAME = 'chat_messages'
                AND COLUMN_NAME = 'quick_replies_json'
        ),
        'ALTER TABLE chat_messages DROP COLUMN quick_replies_json',
        'SELECT 1'
    )
);

PREPARE drop_quick_replies_statement FROM @drop_quick_replies;
EXECUTE drop_quick_replies_statement;
DEALLOCATE PREPARE drop_quick_replies_statement;
