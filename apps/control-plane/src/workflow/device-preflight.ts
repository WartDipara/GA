import { decode, object } from '@game-automation/contracts';
import { ErrorCode, FrameworkError, frameworkError } from '@game-automation/contracts/errors';
import { Host } from '../kernel/host.js';
import type { Fields } from '../utilities/values.js';
import type { Execution } from './execution.js';

/** Check the live instance identity before downloads, without claiming a reservation. */
export async function verifyWorkflowDevices(
  execution: Execution,
  runId: string,
  manifest: Fields,
  instances: number[],
  sourceRun: string | undefined,
  preparationRuns: (string | undefined)[],
): Promise<void> {
  const { store } = execution;
  const host = Host.fromManifest(store, runId, manifest, execution.signal);
  const environment = object(object(manifest['application'])['environment']);
  const devices: Fields[] = [];
  store.command('transition', { run_id: runId, status: 'running', stage: 'device-preflight' });
  try {
    for (const [apkIndex, instanceIndex] of instances.entries()) {
      execution.check(runId);
      const device: Fields = { apk_index: apkIndex, instance_index: instanceIndex, status: 'unavailable' };
      devices.push(device);
      const request = {
        instance_index: instanceIndex,
        command_timeout_seconds: environment['command_timeout_seconds'],
      };
      const prepared = preparationRuns[apkIndex];
      const bound = prepared
        ? store.events(prepared, { kinds: ['device.session.bound'], limit: 1 }).at(-1)
        : undefined;
      const input = decode('emulator-session-request', bound
        ? bound.payload['request']
        : await execution.tool('device_session_request', runId, request));
      const session = decode('emulator-session-result', await host.execute('call', { ...input }, {
        capability: 'emulator.session', timeout: 0,
      }));
      if (session.instance_index !== instanceIndex ||
          (input.instance_id !== null && input.instance_id !== session.instance_id) ||
          (input.serial !== null && input.serial !== session.serial))
        throw new FrameworkError(ErrorCode.DEVICE_IDENTITY_CHANGED);
      // Only a bound preprocessing child identifies the same APK workspace on resume.
      const apkKey = sourceRun ? `${sourceRun}:${apkIndex}` : null;
      if (sourceRun && apkKey) store.assertDeviceWorkspaceAttached(sourceRun, apkKey);
      const { existing, conflicts, transferable } = store.deviceReservation(
        session.instance_id, instanceIndex, apkKey,
      );
      if (conflicts.length) {
        const owners = conflicts.map((binding) => store.deviceReservationOwner(binding));
        device['status'] = 'conflict';
        device['owners'] = owners;
        throw store.deviceReservationError(instanceIndex, owners);
      }
      device['status'] = existing ? 'reserved-for-workspace' : 'available';
      if (existing) device['owners'] = [store.deviceReservationOwner(existing)];
      if (transferable.length) {
        device['status'] = 'available-for-handoff';
        device['owners'] = transferable.map((binding) => store.deviceReservationOwner(binding));
      }
    }
  } catch (error) {
    const result = { devices, error: frameworkError(error).details() };
    store.event(runId, 'workflow.device.preflight', result);
    throw error;
  }
  store.event(runId, 'workflow.device.preflight', { devices });
}
