SELECT e.run_id, e.kind, e.data
FROM events e
JOIN runs r ON r.id = e.run_id
WHERE e.kind IN ('report.requested', 'report.finished', 'task.retention.updated')
ORDER BY e.seq;
