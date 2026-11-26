SELECT r.task_id, i.spec
FROM runs r
JOIN run_inputs i ON i.run_id = r.id;
