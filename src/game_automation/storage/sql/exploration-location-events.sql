SELECT e.run_id, e.span_id, e.data
FROM events e
JOIN runs r ON r.id = e.run_id
WHERE r.task_id = ?
  AND e.seq <= ?
  AND e.kind = 'exploration.location'
ORDER BY e.seq DESC
LIMIT 2000;
