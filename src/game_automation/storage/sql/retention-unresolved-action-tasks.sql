SELECT DISTINCT r.task_id
FROM actions a
JOIN runs r ON r.id = a.run_id
WHERE a.status IN ('prepared', 'unknown', 'executed');
