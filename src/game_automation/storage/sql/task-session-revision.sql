SELECT COALESCE(MAX(seq), 0)
FROM events
WHERE run_id IN (SELECT id FROM runs WHERE task_id = ?);
