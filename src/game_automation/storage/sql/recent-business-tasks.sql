SELECT t.id AS task_id, t.spec, COUNT(r.id) AS executions
FROM tasks t
LEFT JOIN runs r ON r.task_id = t.id
WHERE NOT EXISTS (SELECT 1 FROM task_aliases a WHERE a.alias_id = t.id)
GROUP BY t.id
ORDER BY MAX(r.rowid) DESC, t.rowid DESC
LIMIT ?;
