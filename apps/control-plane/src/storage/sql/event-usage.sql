BEGIN IMMEDIATE;
CREATE TABLE event_usage (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    size_bytes INTEGER NOT NULL
);
INSERT INTO event_usage
    SELECT run_id,SUM(LENGTH(CAST(data AS BLOB))) FROM events GROUP BY run_id;
CREATE TRIGGER event_usage_insert AFTER INSERT ON events BEGIN
    INSERT INTO event_usage VALUES(NEW.run_id,LENGTH(CAST(NEW.data AS BLOB)))
    ON CONFLICT(run_id) DO UPDATE SET size_bytes=size_bytes+excluded.size_bytes;
END;
CREATE TRIGGER event_usage_delete AFTER DELETE ON events BEGIN
    UPDATE event_usage SET size_bytes=size_bytes-LENGTH(CAST(OLD.data AS BLOB))
    WHERE run_id=OLD.run_id;
END;
CREATE TRIGGER event_usage_update AFTER UPDATE ON events BEGIN
    UPDATE event_usage SET size_bytes=size_bytes-LENGTH(CAST(OLD.data AS BLOB))
    WHERE run_id=OLD.run_id;
    INSERT INTO event_usage VALUES(NEW.run_id,LENGTH(CAST(NEW.data AS BLOB)))
    ON CONFLICT(run_id) DO UPDATE SET size_bytes=size_bytes+excluded.size_bytes;
END;
COMMIT;
