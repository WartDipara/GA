SELECT COUNT(*) AS events,
       MIN(seq) AS first_seq,
       MAX(seq) AS last_seq,
       SUM(CASE WHEN kind = 'call.started' THEN 1 ELSE 0 END) AS calls,
       SUM(CASE
           WHEN kind = 'call.started' AND json_extract(data, '$.payload.capability') = 'ai.decide'
           THEN 1 ELSE 0
       END) AS model_calls
FROM events
WHERE run_id = ? AND seq <= ?;
