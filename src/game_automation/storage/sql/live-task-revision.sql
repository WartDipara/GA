SELECT COALESCE(MAX((SELECT MAX(seq) FROM events WHERE run_id = r.id)), 0)
FROM runs r
WHERE r.task_id = ?;
