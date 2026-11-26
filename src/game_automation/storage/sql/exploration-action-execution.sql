SELECT a.run_id
FROM actions a
JOIN runs r ON r.id = a.run_id
WHERE a.action_id = ? AND r.task_id = ?;
