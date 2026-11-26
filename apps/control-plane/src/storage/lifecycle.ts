import { object, text } from '@game-automation/contracts';
import type { Store } from './store.js';
import { integer } from './values.js';

/** Lookup stays inside the task and APK workspace; it never changes action outcomes. */
export function previousClose(store: Store, runId: string): boolean {
  const task = store.input(runId);
  const scope = {
    taskId: text(store.run(runId)['task_id']),
    sourceRun: text(task['source_run']),
    apkIndex: integer(task['apk_index']),
    instanceId: text(task['instance_id']),
    target: text(task['target']),
    sha256: text(object(task['metadata'])['sha256']),
  };
  return store.workflows.hasPreviousClose(scope);
}
