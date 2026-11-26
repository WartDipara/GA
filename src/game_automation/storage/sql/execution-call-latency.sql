WITH endings AS (
    SELECT span_id, MAX(seq) AS last_seq
    FROM events
    WHERE run_id = :run_id
      AND kind IN ('call.completed', 'call.failed', 'call.incomplete')
    GROUP BY span_id
)
SELECT s.seq, s.span_id, s.data, e.data AS ending
FROM events s
LEFT JOIN endings f ON f.span_id = s.span_id
LEFT JOIN events e ON e.seq = f.last_seq
WHERE s.run_id = :run_id AND s.kind = 'call.started'
ORDER BY s.seq;
