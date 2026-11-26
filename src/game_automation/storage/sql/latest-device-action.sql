SELECT a.run_id, a.action_id, a.status, a.result
FROM actions a
JOIN device_bindings b ON json_extract(a.result, '$.instance_id') = b.instance_id
JOIN run_inputs i ON i.run_id = a.run_id
WHERE b.apk_key = :apk_key
  AND json_extract(i.spec, '$.source_run') || ':' || json_extract(i.spec, '$.apk_index') = b.apk_key
  AND (
      :execution_kind = 'gameplay'
      OR json_extract(a.result, '$.capability') IN ('device.install', 'device.uninstall')
  )
ORDER BY a.rowid DESC
LIMIT 1;
