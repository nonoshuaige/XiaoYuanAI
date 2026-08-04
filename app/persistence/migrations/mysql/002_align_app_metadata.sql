ALTER TABLE app_metadata
    CHANGE COLUMN metadata_key `key` VARCHAR(128) NOT NULL,
    CHANGE COLUMN metadata_value `value` LONGTEXT NOT NULL;
