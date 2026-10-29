import { object, text } from '@game-automation/contracts';
import type { Store } from './store.js';
import { integer } from './values.js';

/** Lookup stays inside the task and APK workspace; it never changes action outcomes. */
export function previousClose(store: Store, runId: string): boolean {
  const task = store.input(runId);
  const row = store.database.one(
    `SELECT 1 FROM events e JOIN runs r ON r.id=e.run_id
     JOIN run_inputs i ON i.run_id=e.run_id
     WHERE r.task_id=? AND e.kind='gameplay.lifecycle.started'
       AND json_extract(e.data,'$.payload.operation')='close'
       AND json_extract(i.spec,'$.source_run')=?
       AND json_extract(i.spec,'$.apk_index')=?
       AND json_extract(i.spec,'$.instance_id')=?
       AND json_extract(i.spec,'$.target')=?
       AND json_extract(e.data,'$.payload.sha256')=? LIMIT 1`,
    text(store.run(runId)['task_id']),
    text(task['source_run']),
    integer(task['apk_index']),
    text(task['instance_id']),
    text(task['target']),
    text(object(task['metadata'])['sha256']),
  );
  return !!row;
}
