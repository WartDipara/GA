SELECT t.id AS task_id,
       r.id AS run_id,
       r.mode,
       r.status,
       json_extract(i.spec, '$.metadata.apkName') AS title,
       ROW_NUMBER() OVER (PARTITION BY t.id ORDER BY r.rowid) AS ordinal
FROM tasks t
LEFT JOIN runs r ON r.task_id = t.id
LEFT JOIN run_inputs i ON i.run_id = r.id
WHERE NOT EXISTS (SELECT 1 FROM task_aliases a WHERE a.alias_id = t.id)
ORDER BY t.rowid DESC, r.rowid DESC;
