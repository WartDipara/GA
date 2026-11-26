WITH ranked AS (
    SELECT run_id,
           span_id,
           data,
           COUNT(*) OVER (PARTITION BY run_id) AS total,
           ROW_NUMBER() OVER (PARTITION BY run_id ORDER BY seq DESC) AS position
    FROM events
    WHERE run_id IN (SELECT value FROM json_each(?))
      AND kind = 'execution.segment.started'
)
SELECT *
FROM ranked
WHERE position <= 20;
