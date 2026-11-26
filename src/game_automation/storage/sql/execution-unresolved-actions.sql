SELECT action_id, status, result
FROM actions
WHERE run_id = ?
  AND status IN ('prepared', 'unknown', 'executed');
