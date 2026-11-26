SELECT e.seq,
       e.run_id,
       e.kind,
       e.span_id,
       json_extract(e.data, '$.parent_span_id') AS parent_span_id,
       json_extract(e.data, '$.timestamp') AS timestamp,
       json_extract(e.data, '$.payload.capability') AS capability,
       json_extract(e.data, '$.payload.duration_ms') AS duration_ms,
       substr(json_extract(e.data, '$.payload'), 1, 4001) AS detail
FROM events e
JOIN runs r ON r.id = e.run_id
WHERE r.task_id = :task_id
  AND e.seq > :after
  AND (
      e.kind LIKE 'call.%'
      OR e.kind = 'run.state'
      OR e.kind LIKE 'workflow.stage.%'
      OR e.kind LIKE 'action.%'
      OR e.kind LIKE 'exploration.supervision.%'
      OR e.kind LIKE 'gameplay.watchdog.%'
      OR e.kind LIKE 'gameplay.lifecycle.%'
      OR e.kind = 'gameplay.finalization.issue'
      OR e.kind IN ('report.requested', 'report.finished')
  )
ORDER BY e.seq ASC
LIMIT 201;
