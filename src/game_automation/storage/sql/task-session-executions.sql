SELECT r.rowid AS cursor, r.id, r.mode, r.status, i.spec
FROM runs r
JOIN run_inputs i ON i.run_id = r.id
WHERE r.task_id = :task_id
  AND (:before = 0 OR r.rowid < :before)
ORDER BY r.rowid DESC
LIMIT :page_size;
