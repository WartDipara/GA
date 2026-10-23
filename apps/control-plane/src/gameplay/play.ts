import { readFile } from 'node:fs/promises';
import { relative } from 'node:path';
import { decode, object, text } from '@game-automation/contracts';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import { Host } from '../kernel/host.js';
import { within, type Fields } from '../storage/values.js';
import type { Execution } from '../workflow/execution.js';
import { executeRoutine, Perception } from './local.js';
import { runLoop } from './loop.js';
import { ProgressStore } from './progress.js';
import { confirmSession, Runtime } from './runtime.js';
import type { Observation } from './types.js';

function sameProcess(before: Observation['runtime'], after: Observation['runtime']): boolean {
  const first = before.main_process;
  const second = after.main_process;
  return (
    before.boot_id === after.boot_id &&
    first !== null &&
    second !== null &&
    first.pid === second.pid &&
    first.uid === second.uid &&
    first.start_ticks === second.start_ticks
  );
}

async function reconcile(runtime: Runtime, observation: Observation): Promise<void> {
  const previous = runtime.store.latestAction(runtime.runId);
  const progress = runtime.progress.data;
  if (previous && ['verified', 'reconciled'].includes(text(previous['status']))) {
    for (const path of progress.paths)
      if (path.action_id === previous['action_id'])
        path.status = text(object(previous['result'] ?? {})['status'] ?? previous['status']);
  }
  let resolved = true;
  if (previous && ['unknown', 'executed', 'prepared'].includes(text(previous['status']))) {
    const priorRun = text(previous['run_id']);
    if (runtime.store.run(priorRun)['task_id'] !== runtime.store.run(runtime.runId)['task_id'])
      throw new FrameworkError(ErrorCode.INVALID_PATH);
    if (previous['capability'] !== 'device.execute')
      throw new FrameworkError(
        ErrorCode.DEVICE_INSTALL_UNKNOWN,
        'Reconcile installation in the preparation stage before gameplay',
      );
    const directory = runtime.store.runDirectory(priorRun);
    const raw: unknown = JSON.parse(
      await readFile(within(directory, text(previous['input_ref'])), 'utf8'),
    );
    const intent = decode('device-execute-request', raw);
    const action = intent.action;
    const focused = observation.runtime.focused_display;
    let canReconcile =
      ['return', 'launch'].includes(action.type) &&
      observation.runtime.alive &&
      focused?.package_name === runtime.task['target'];
    if (canReconcile && action.type === 'return')
      canReconcile = sameProcess(intent.runtime, observation.runtime);
    const passive = action.type === 'wait';
    const actionId = text(previous['action_id']);
    if (previous['status'] === 'prepared')
      runtime.outcome(priorRun, actionId, 'rejected', { dispatched: false });
    else if (canReconcile || passive) {
      runtime.outcome(priorRun, actionId, passive ? 'reconciled' : 'verified', {
        observation_run: runtime.runId,
        observation_id: observation.observation_id,
        business_goal_verified: false,
      });
      runtime.record('gameplay.action.reconciled', { action_id: actionId, action_run: priorRun });
    } else if (
      !['capture', 'activate-game', 'return-to-game', 'routine'].includes(
        text(runtime.task['goal']),
      ) &&
      previous['capability'] === 'device.execute'
    ) {
      const imagePath = within(directory, intent.observation_path);
      const pending = progress.pending ?? {
        observation_id: 'interrupted-observation',
        action,
        expected_outcome: 'Reconcile interrupted action',
        image_ref: relative(directory, imagePath).replaceAll('\\', '/'),
        run_id: priorRun,
      };
      if (pending.action_id && pending.action_id !== actionId)
        throw new FrameworkError(
          ErrorCode.ACTION_ALREADY_DISPATCHED,
          'Checkpoint and action ledger disagree',
        );
      pending.run_id = priorRun;
      pending.action_id = actionId;
      progress.pending = pending;
      resolved = false;
    } else
      throw new FrameworkError(
        ErrorCode.ACTION_ALREADY_DISPATCHED,
        'Fresh observation saved; pending action requires reconciliation',
      );
  }
  if (resolved) progress.pending = null;
  runtime.progress.save();
}

async function playBound(runtime: Runtime): Promise<void> {
  let observation = await runtime.observe();
  runtime.checkpoint(observation);
  runtime.diagnostics.request();
  await reconcile(runtime, observation);
  const goal = text(runtime.task['goal']);
  if (goal === 'activate-game' || goal === 'return-to-game') {
    runtime.begin(goal);
    const action = decode('gameplay-action', {
      type: goal === 'activate-game' && !observation.runtime.alive ? 'launch' : 'return',
      x: 0,
      y: 0,
      end_x: 0,
      end_y: 0,
      duration_ms: 1,
      bounds: null,
      credential_field: null,
    });
    const actionId = await runtime.perform(action, observation);
    const after = await runtime.observe();
    const focused = after.runtime.focused_display;
    const verified =
      after.runtime.alive &&
      focused?.package_name === runtime.task['target'] &&
      (action.type !== 'return' || sameProcess(observation.runtime, after.runtime));
    const outcome = { observation_id: after.observation_id, verified };
    runtime.record('gameplay.outcome', outcome);
    if (!verified) throw new FrameworkError(ErrorCode.GAMEPLAY_BLOCKED);
    runtime.outcome(runtime.runId, actionId, 'verified', outcome);
    runtime.progress.data.pending = null;
    runtime.checkpoint(after);
  } else if (goal === 'routine') {
    const routine = decode('gameplay-routine', { steps: runtime.task['actions'] });
    await executeRoutine(runtime, observation, routine.steps);
  } else if (goal === 'capture') {
    const view = await new Perception(runtime).settle(observation);
    observation = view.observation;
    runtime.record('gameplay.route', {
      route: 'plan',
      reason: 'unknown-state-or-new-goal',
      screen_key: view.facts.screen_key,
    });
  } else await runLoop(runtime, observation);
}

export async function play(
  execution: Execution,
  runId: string,
  manifest: Fields,
  task: Fields,
): Promise<void> {
  const store = execution.store;
  const providers: Fields = {};
  for (const value of Object.values(object(manifest['plugins']))) {
    const plugin = object(value);
    const capabilities = plugin['capabilities'];
    if (!Array.isArray(capabilities)) throw new FrameworkError(ErrorCode.INVALID_PLUGIN);
    for (const capability of capabilities) {
      const name = text(capability);
      if (providers[name]) throw new FrameworkError(ErrorCode.AMBIGUOUS_CAPABILITY);
      providers[name] = { ...plugin };
    }
  }
  const host = new Host(
    store,
    runId,
    { application: manifest['application'], providers },
    execution.signal,
  );
  const call = (capability: string, input: Fields): Promise<unknown> =>
    host.execute('call', input, {
      capability,
      timeout: /^(device|emulator)\./.test(capability) ? 0 : null,
    });
  const previous = store.events(runId, { kinds: ['device.session.bound'], limit: 1 }).at(-1);
  const request = previous
    ? object(object(previous.payload)['request'])
    : {
        manager_executable: process.env['GAME_AUTO_MUMU_MANAGER'] || null,
        adb_executable: process.env['GAME_AUTO_ADB'] || null,
        instance_index: task['instance_index'],
        instance_id: null,
        serial: null,
        command_timeout_seconds: object(object(manifest['application'])['environment'])[
          'command_timeout_seconds'
        ],
      };
  const actual = decode('emulator-session-result', await call('emulator.session', request));
  if (
    actual.instance_index !== task['instance_index'] ||
    actual.instance_id !== task['instance_id'] ||
    (request['instance_id'] != null && request['instance_id'] !== actual.instance_id) ||
    (request['serial'] != null && request['serial'] !== actual.serial)
  )
    throw new FrameworkError(ErrorCode.DEVICE_IDENTITY_CHANGED);
  const session = {
    ...request,
    instance_id: actual.instance_id,
    serial: actual.serial,
    manager_executable: actual.manager_executable ?? request['manager_executable'],
    adb_executable: actual.adb_executable,
  };
  if (!previous) store.event(runId, 'device.session.bound', { request: session, session: actual });
  store.command('reserve_device', {
    run_id: runId,
    instance_id: actual.instance_id,
    instance_index: actual.instance_index,
    apk_key: `${text(task['source_run'])}:${String(task['apk_index'])}`,
  });
  const installed = decode(
    'device-inspect-result',
    await call('device.inspect', { session, package_name: task['target'] }),
  );
  confirmSession(session, installed.session);
  if (installed.package_name !== task['target'])
    throw new FrameworkError(ErrorCode.INVALID_ADB_OUTPUT);
  store.event(runId, 'device.package.observed', installed);
  if (installed.sha256 !== object(task['metadata'])['sha256'])
    throw new FrameworkError(ErrorCode.DEVICE_PACKAGE_CONFLICT);
  const profile = text(task['credential_profile']);
  if (!/^[a-z0-9_-]{1,64}$/.test(profile))
    throw new FrameworkError(ErrorCode.CREDENTIAL_UNAVAILABLE);
  const credentialPath = within(store.runDirectory(runId), `credentials/${profile}.json`);
  const credential = decode(
    'credential-prepare-result',
    await call('credential.prepare', {
      profile_path: within(store.root, `var/secrets/${profile}.json`),
      destination: credentialPath,
    }),
  );
  const progress = new ProgressStore(store, runId, task, credential.revision);
  progress.save();
  const runtime = new Runtime(
    execution,
    runId,
    task,
    manifest,
    session,
    credentialPath,
    progress,
    host,
  );
  try {
    await runtime.admit(true);
    await playBound(runtime);
    runtime.finish();
  } catch (error) {
    runtime.finish(error);
    throw error;
  } finally {
    await runtime.diagnostics.close();
  }
}
