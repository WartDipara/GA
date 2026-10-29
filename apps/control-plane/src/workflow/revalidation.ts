import { decode, object, text } from '@game-automation/contracts';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import { Host } from '../kernel/host.js';
import { confirmSession } from '../gameplay/runtime.js';
import type { Fields } from '../storage/values.js';
import type { Execution } from './execution.js';

/** Historical stage success does not establish the current device state. */
export async function revalidateDeviceStage(
  execution: Execution,
  runId: string,
  child: string,
  span: string,
  kind: 'installation' | 'foreground',
): Promise<boolean> {
  const store = execution.store;
  const task = store.input(child);
  const bound = store.events(child, { kinds: ['device.session.bound'], limit: 1 }).at(-1);
  if (!bound) throw new FrameworkError(ErrorCode.DEVICE_IDENTITY_CHANGED);
  const session = object(bound.payload['request']);
  const manifest = await execution.manifest(runId);
  const host = Host.fromManifest(store, runId, manifest, execution.signal);
  const call = (capability: string, input: Fields): Promise<unknown> =>
    host.execute('call', input, { capability, timeout: 0, parent_span_id: span });
  const actual = decode('emulator-session-result', await call('emulator.session', session));
  confirmSession(session, object(actual));
  if (actual.instance_index !== task['instance_index'])
    throw new FrameworkError(ErrorCode.DEVICE_IDENTITY_CHANGED);
  const request = { session, package_name: task['target'] };
  const installed = decode('device-inspect-result', await call('device.inspect', request));
  confirmSession(session, installed.session);
  if (installed.package_name !== task['target'])
    throw new FrameworkError(ErrorCode.INVALID_ADB_OUTPUT);
  store.event(
    runId,
    'workflow.device.observed',
    { child_run: child, ...installed },
    { parent_span_id: span },
  );
  if (installed.installed && installed.sha256 !== object(task['metadata'])['sha256'])
    throw new FrameworkError(ErrorCode.DEVICE_PACKAGE_CONFLICT);
  let reusable = installed.installed;
  if (reusable && kind === 'foreground') {
    const observed = decode('device-runtime-result', await call('device.runtime', request));
    confirmSession(session, observed.session);
    if (observed.runtime.package_name !== task['target'])
      throw new FrameworkError(ErrorCode.INVALID_ADB_OUTPUT);
    reusable =
      observed.runtime.alive && observed.runtime.focused_display?.package_name === task['target'];
    store.event(
      runId,
      'workflow.runtime.observed',
      { child_run: child, ...observed },
      { parent_span_id: span },
    );
  }
  const result = {
    child_run: child,
    kind,
    reusable,
    reason: reusable
      ? 'current-device-state-matches'
      : installed.installed
        ? 'target-not-in-foreground'
        : 'package-not-installed',
    target: text(task['target']),
  };
  store.event(runId, 'workflow.stage.revalidated', result, { parent_span_id: span });
  return reusable;
}
