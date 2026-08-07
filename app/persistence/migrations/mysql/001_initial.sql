CREATE TABLE IF NOT EXISTS app_metadata (
    metadata_key VARCHAR(128) PRIMARY KEY,
    metadata_value LONGTEXT NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS sessions (
    session_id VARCHAR(64) PRIMARY KEY,
    title VARCHAR(80) NOT NULL DEFAULT '新对话',
    round_count INT NOT NULL DEFAULT 0,
    created_at VARCHAR(40) NOT NULL,
    updated_at VARCHAR(40) NOT NULL,
    INDEX idx_sessions_updated (updated_at DESC)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS conversation_rounds (
    session_id VARCHAR(64) NOT NULL,
    round_no INT NOT NULL,
    model_id VARCHAR(256) NULL,
    job_owner VARCHAR(32) NOT NULL DEFAULT 'default',
    status VARCHAR(16) NOT NULL,
    error TEXT NULL,
    created_at VARCHAR(40) NOT NULL,
    completed_at VARCHAR(40) NULL,
    PRIMARY KEY (session_id, round_no),
    INDEX idx_rounds_session_status (session_id, status, round_no),
    CONSTRAINT fk_rounds_session FOREIGN KEY (session_id)
        REFERENCES sessions(session_id) ON DELETE CASCADE,
    CONSTRAINT chk_round_status CHECK (status IN ('pending', 'completed', 'failed'))
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS chat_events (
    event_id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    round_no INT NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    payload_json LONGTEXT NOT NULL DEFAULT ('{}'),
    created_at VARCHAR(40) NOT NULL,
    INDEX idx_chat_events_round (session_id, round_no, event_id),
    CONSTRAINT fk_events_round FOREIGN KEY (session_id, round_no)
        REFERENCES conversation_rounds(session_id, round_no) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS model_call_audits (
    session_id VARCHAR(64) NOT NULL,
    round_no INT NOT NULL,
    model_id VARCHAR(256) NOT NULL,
    status VARCHAR(16) NOT NULL,
    provider_responses_json LONGTEXT NOT NULL DEFAULT ('[]'),
    langchain_ai_message_json LONGTEXT NULL,
    error TEXT NULL,
    created_at VARCHAR(40) NOT NULL,
    PRIMARY KEY (session_id, round_no),
    INDEX idx_model_audits_session_round (session_id, round_no DESC),
    CONSTRAINT fk_audits_round FOREIGN KEY (session_id, round_no)
        REFERENCES conversation_rounds(session_id, round_no) ON DELETE CASCADE,
    CONSTRAINT chk_audit_status CHECK (status IN ('completed', 'failed'))
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS chat_messages (
    session_id VARCHAR(64) NOT NULL,
    round_no INT NOT NULL,
    role VARCHAR(16) NOT NULL,
    content LONGTEXT NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    PRIMARY KEY (session_id, round_no, role),
    INDEX idx_chat_messages_session_round (session_id, round_no),
    CONSTRAINT fk_messages_session FOREIGN KEY (session_id)
        REFERENCES sessions(session_id) ON DELETE CASCADE,
    CONSTRAINT chk_message_role CHECK (role IN ('user', 'assistant'))
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS conversation_summaries (
    summary_id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    content LONGTEXT NOT NULL,
    start_round INT NOT NULL,
    end_round INT NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    UNIQUE KEY uq_summaries_session_end (session_id, end_round),
    INDEX idx_summaries_session_end (session_id, end_round DESC),
    CONSTRAINT fk_summaries_session FOREIGN KEY (session_id)
        REFERENCES sessions(session_id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS people (
    employee_id VARCHAR(32) PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    phone VARCHAR(32) NOT NULL UNIQUE,
    department VARCHAR(80) NOT NULL,
    INDEX idx_people_name (name),
    INDEX idx_people_department_name (department, name)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS meeting_rooms (
    room_id VARCHAR(80) PRIMARY KEY,
    room_name VARCHAR(100) NOT NULL,
    floor VARCHAR(8) NOT NULL,
    capacity INT NOT NULL,
    equipment_json LONGTEXT NOT NULL,
    INDEX idx_meeting_rooms_floor (floor, room_name),
    CONSTRAINT chk_room_capacity CHECK (capacity > 0)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS meeting_room_bookings (
    booking_id VARCHAR(64) PRIMARY KEY,
    meeting_id VARCHAR(64) NOT NULL UNIQUE,
    room_id VARCHAR(80) NOT NULL,
    booking_date VARCHAR(10) NOT NULL,
    start_time VARCHAR(5) NOT NULL,
    end_time VARCHAR(5) NOT NULL,
    capacity INT NOT NULL,
    theme VARCHAR(100) NOT NULL,
    booked_by VARCHAR(100) NOT NULL,
    source VARCHAR(16) NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    INDEX idx_meeting_room_bookings_slot
        (booking_date, room_id, start_time, end_time),
    CONSTRAINT fk_bookings_room FOREIGN KEY (room_id)
        REFERENCES meeting_rooms(room_id) ON DELETE CASCADE,
    CONSTRAINT chk_booking_capacity CHECK (capacity > 0),
    CONSTRAINT chk_booking_source CHECK (source IN ('sample', 'interactive'))
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS meeting_room_booking_drafts (
    draft_id VARCHAR(64) PRIMARY KEY,
    session_id VARCHAR(64) NULL,
    round_no INT NULL,
    room_id VARCHAR(80) NOT NULL,
    floor VARCHAR(8) NOT NULL,
    booking_date VARCHAR(10) NOT NULL,
    start_time VARCHAR(5) NOT NULL,
    end_time VARCHAR(5) NOT NULL,
    capacity INT NOT NULL,
    theme VARCHAR(100) NOT NULL,
    booked_by VARCHAR(100) NOT NULL,
    status VARCHAR(16) NOT NULL,
    booking_id VARCHAR(64) NULL,
    meeting_id VARCHAR(64) NULL,
    created_at VARCHAR(40) NOT NULL,
    updated_at VARCHAR(40) NOT NULL,
    expires_at VARCHAR(40) NOT NULL,
    INDEX idx_meeting_room_drafts_session (session_id, round_no, created_at),
    CONSTRAINT fk_drafts_room FOREIGN KEY (room_id)
        REFERENCES meeting_rooms(room_id) ON DELETE CASCADE,
    CONSTRAINT chk_draft_capacity CHECK (capacity > 0),
    CONSTRAINT chk_draft_status CHECK (
        status IN ('pending', 'confirmed', 'cancelled', 'expired')
    )
) ENGINE=InnoDB;
