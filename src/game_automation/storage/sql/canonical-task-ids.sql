SELECT id
FROM tasks
WHERE id NOT IN (SELECT alias_id FROM task_aliases);
