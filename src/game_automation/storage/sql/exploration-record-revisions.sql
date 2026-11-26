SELECT e.run_id, e.data
FROM events e
JOIN runs r ON r.id = e.run_id
WHERE r.task_id = ?
  AND e.seq <= ?
  AND e.kind LIKE 'exploration.%.updated'
ORDER BY e.seq;
