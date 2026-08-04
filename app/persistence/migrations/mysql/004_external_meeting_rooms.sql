ALTER TABLE meeting_room_booking_drafts
    DROP FOREIGN KEY fk_drafts_room;

ALTER TABLE meeting_room_booking_drafts
    ADD COLUMN room_name VARCHAR(100) NULL AFTER room_id;

UPDATE meeting_room_booking_drafts AS draft
JOIN meeting_rooms AS room ON room.room_id = draft.room_id
SET draft.room_name = room.room_name
WHERE draft.room_name IS NULL;
