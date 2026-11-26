SELECT seq,data FROM events
WHERE run_id=:run_id AND seq>:after AND seq<=:through
  AND (:span IS NULL OR span_id=:span)
  AND (:kinds IS NULL OR kind IN (SELECT value FROM json_each(:kinds)))
ORDER BY seq;
