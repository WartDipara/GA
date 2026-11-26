SELECT r.id, r.task_id, r.status, r.updated_at, i.spec
FROM runs r
JOIN run_inputs i ON i.run_id = r.id
WHERE r.task_id NOT IN (SELECT alias_id FROM task_aliases);
