SELECT COUNT(*)
FROM tasks t
WHERE NOT EXISTS (SELECT 1 FROM task_aliases a WHERE a.alias_id = t.id);
