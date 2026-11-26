WITH metadata AS (
    SELECT r.task_id,
           json_extract(i.spec, '$.metadata.apkName') AS title,
           json_extract(i.spec, '$.metadata.packageName') AS package_name,
           ROW_NUMBER() OVER (PARTITION BY r.task_id ORDER BY r.rowid DESC) AS position
    FROM run_inputs i
    JOIN runs r ON r.id = i.run_id
    WHERE json_extract(i.spec, '$.metadata.apkName') IS NOT NULL
)
SELECT t.id AS task_id,
       COUNT(r.id) AS executions,
       MIN(r.created_at) AS created_at,
       MAX(r.updated_at) AS updated_at,
       SUM(CASE WHEN r.status = 'running' THEN 1 ELSE 0 END) AS running,
       SUM(CASE WHEN r.status IN ('blocked', 'failed') THEN 1 ELSE 0 END) AS issues,
       COALESCE(m.title, 'Game testing task') AS title,
       m.package_name
FROM tasks t
LEFT JOIN runs r ON r.task_id = t.id
LEFT JOIN metadata m ON m.task_id = t.id AND m.position = 1
WHERE NOT EXISTS (SELECT 1 FROM task_aliases a WHERE a.alias_id = t.id)
GROUP BY t.id
ORDER BY MAX(r.rowid) DESC, t.rowid DESC;
