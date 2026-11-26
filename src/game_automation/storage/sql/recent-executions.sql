SELECT r.id AS run_id, r.task_id, r.mode, r.status, r.stage, r.created_at, i.spec
FROM runs r
JOIN run_inputs i ON i.run_id = r.id
ORDER BY r.rowid DESC
LIMIT ?;
