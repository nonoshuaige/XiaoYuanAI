DROP TABLE IF EXISTS chat_events;

DROP TABLE IF EXISTS app_metadata;

ALTER TABLE model_call_audits
    DROP INDEX idx_model_audits_session_round,
    DROP CHECK chk_audit_status,
    DROP COLUMN model_id,
    DROP COLUMN status,
    DROP COLUMN error;

DELETE draft
FROM meeting_room_booking_drafts AS draft
LEFT JOIN sessions AS session
    ON session.session_id = draft.session_id
WHERE draft.session_id IS NOT NULL
    AND session.session_id IS NULL;

ALTER TABLE meeting_room_booking_drafts
    ADD CONSTRAINT fk_drafts_session FOREIGN KEY (session_id)
        REFERENCES sessions(session_id) ON DELETE CASCADE;
