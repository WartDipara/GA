SELECT run_id,span_id,data,kind
FROM events
WHERE run_id IN (SELECT value FROM json_each(?))
  AND kind IN ('execution.segment.finished', 'execution.segment.incomplete', 'run.continued_from')
ORDER BY seq;
