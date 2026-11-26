SELECT event_id
FROM events
WHERE run_id = ?
  AND event_id IN (SELECT value FROM json_each(?));
