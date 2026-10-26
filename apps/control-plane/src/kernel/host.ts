import { performance } from 'node:perf_hooks';
import { createInterface } from 'node:readline';
import { setTimeout as delay } from 'node:timers/promises';
import { decode, object, text, type ContractMap } from '@game-automation/contracts';
import { ErrorCode, FrameworkError, frameworkError } from '@game-automation/contracts/errors';
import { Peer } from '@game-automation/plugin-sdk/rpc';
import { start, terminate } from '@game-automation/plugin-sdk/process';
import { Store, type TraceIds } from '../storage/store.js';
import {
  canonical,
  integer,
  optionalText,
  redact,
  strings,
  uid,
  type Fields,
} from '../storage/values.js';

const capabilityContracts: Record<string, [keyof ContractMap, keyof ContractMap]> = {
  'credential.prepare': ['credential-prepare-request', 'credential-prepare-result'],
  'planner.image': ['planner-image-request', 'planner-image-result'],
  'ai.decide': ['ai-decide-request', 'ai-decide-result'],
  'task.intake': ['intake-request', 'intake-result'],
  'environment.check': ['environment-request', 'environment-result'],
  'apk.prepare': ['apk-prepare-request', 'apk-metadata'],
  'apk.plan': ['apk-plan-request', 'apk-download-plan'],
  'apk.preflight': ['apk-preflight-request', 'apk-preflight-result'],
  'emulator.discover': ['emulator-discovery-request', 'emulator-discovery-result'],
  'emulator.session': ['emulator-session-request', 'emulator-session-result'],
  'emulator.activate': ['emulator-activate-request', 'emulator-activate-result'],
  'device.inspect': ['device-inspect-request', 'device-inspect-result'],
  'device.install': ['device-install-request', 'device-install-result'],
  'device.observe': ['device-observe-request', 'device-observe-result'],
  'device.runtime': ['device-runtime-request', 'device-runtime-result'],
  'device.capture': ['device-capture-request', 'device-capture-result'],
  'device.execute': ['device-execute-request', 'device-execute-result'],
  'device.logs': ['device-logs-request', 'device-logs-result'],
  'perception.analyze': ['perception-request', 'perception-result'],
  'perception.locate': ['perception-locate-request', 'perception-locate-result'],
  'perception.viewport': ['perception-viewport-request', 'perception-viewport-result'],
};
type Plugin = ContractMap['plugin'] & { cwd: string };
type CallOptions = {
  parent: string | null;
  timeout: number | null;
  ancestry: string[];
  span: string | null;
  action: string | null;
  signal: AbortSignal;
};
const mutations = new Set(['device.install', 'device.execute', 'emulator.activate']);

export class Host {
  readonly artifacts = new Set<string>();
  private readonly providers = new Map<string, Plugin>();
  private readonly application: Fields;
  private readonly workflowRun: string | null;
  failure: FrameworkError | undefined;

  constructor(
    private readonly store: Store,
    readonly runId: string,
    configuration: Fields,
    private readonly signal: AbortSignal,
  ) {
    this.application = object(configuration['application']);
    this.workflowRun = optionalText(store.input(runId)['workflow_run']);
    for (const [capability, value] of Object.entries(object(configuration['providers']))) {
      const manifest = object(value);
      const cwd = manifest['cwd'];
      delete manifest['cwd'];
      delete manifest['runtime'];
      const plugin = { ...decode('plugin', manifest), cwd: text(cwd) };
      this.providers.set(capability, plugin);
    }
  }

  private check(signal: AbortSignal = this.signal): void {
    if (this.failure) throw this.failure;
    if (this.store.database.failure) throw this.store.database.failure;
    this.store.checkCleanup();
    if (
      this.signal.aborted ||
      signal.aborted ||
      this.store.run(this.runId)['cancel_requested'] ||
      (this.workflowRun && this.store.run(this.workflowRun)['cancel_requested'])
    )
      throw new FrameworkError(ErrorCode.CANCELLED);
  }

  private record(kind: string, payload: Fields, ids: TraceIds): void {
    try {
      this.store.event(this.runId, kind, payload, ids);
      this.store.health(this.runId);
    } catch (error) {
      this.failure ??= frameworkError(error);
      throw this.failure;
    }
  }

  private artifact(reference: string, value: unknown): string {
    const path = this.store.artifact(this.runId, reference, value);
    this.artifacts.add(path);
    return path;
  }

  private resolve(capability: string): Plugin {
    const plugin = this.providers.get(capability);
    if (!plugin)
      throw new FrameworkError(
        ErrorCode.MISSING_CAPABILITY,
        `Required capability unavailable: ${capability}`,
      );
    for (const dependency of plugin.required_capabilities) {
      if (!this.providers.has(dependency))
        throw new FrameworkError(
          ErrorCode.MISSING_CAPABILITY,
          `${plugin.name} requires ${dependency}`,
        );
    }
    return plugin;
  }

  async execute(
    operation: string,
    input: Fields,
    context: Fields,
    signal = this.signal,
  ): Promise<unknown> {
    const options: CallOptions = {
      parent: optionalText(context['parent_span_id']),
      timeout: context['timeout'] == null ? null : integer(context['timeout']),
      ancestry: strings(context['ancestry'] ?? []),
      span: optionalText(context['span_id']),
      action: null,
      signal,
    };
    if (operation === 'call') return this.call(text(context['capability']), input, options);
    if (operation !== 'install' && operation !== 'perform')
      throw new FrameworkError(ErrorCode.METHOD_NOT_FOUND);
    const capability = operation === 'install' ? 'device.install' : 'device.execute';
    this.check();
    if (!this.resolve(capability).mutating.includes(capability))
      throw new FrameworkError(ErrorCode.CAPABILITY_DENIED);
    const contract = capabilityContracts[capability];
    if (contract) decode(contract[0], input);
    const maximum = operation === 'install' ? integer(this.application['max_actions']) : null;
    if (maximum !== null && maximum <= 0) throw new FrameworkError(ErrorCode.BUDGET_EXHAUSTED);
    const span = uid('span');
    const reference = this.artifact(
      `artifacts/${uid(operation === 'install' ? 'install' : 'action')}/input.json`,
      input,
    );
    const intent = {
      run_id: this.runId,
      instance_id: text(object(input['session'])['instance_id']),
      input_ref: reference,
      max_actions: maximum,
      span_id: span,
      capability,
    };
    const action = text(this.store.command('begin_device_action', intent));
    try {
      const result = object(
        await this.call(capability, input, { ...options, span, action, timeout: 0 }),
      );
      if (this.store.latestAction(this.runId)?.['status'] !== 'unknown')
        throw new FrameworkError(ErrorCode.CAPABILITY_DENIED);
      const status =
        operation === 'install' && result['status'] !== 'installed' ? 'rejected' : 'executed';
      this.store.command('finish_device_action', {
        run_id: this.runId,
        action_id: action,
        status,
        result,
      });
      const output = operation === 'perform' ? { ...result, action_id: action } : result;
      return output;
    } catch (error) {
      if (this.store.latestAction(this.runId)?.['status'] === 'prepared') {
        const result = { error: frameworkError(error).details(), dispatched: false };
        this.store.command('finish_device_action', {
          run_id: this.runId,
          action_id: action,
          status: 'rejected',
          result,
        });
      }
      throw error;
    }
  }

  private async call(capability: string, input: Fields, options: CallOptions): Promise<unknown> {
    this.check(options.signal);
    if (options.ancestry.includes(capability) || options.ancestry.length >= 8)
      throw new FrameworkError(ErrorCode.DEPENDENCY_CYCLE);
    const plugin = this.resolve(capability);
    const mutating = plugin.mutating.includes(capability);
    if (mutations.has(capability) && !mutating)
      throw new FrameworkError(ErrorCode.CAPABILITY_DENIED);
    if (mutating) {
      const action = this.store.latestAction(this.runId);
      const delegated = capability === 'emulator.activate';
      if (
        !mutations.has(capability) ||
        !action ||
        action['action_id'] !== options.action ||
        action['status'] !== (delegated ? 'unknown' : 'prepared') ||
        action['run_id'] !== this.runId ||
        (delegated && canonical(options.ancestry) !== canonical(['device.execute']))
      )
        throw new FrameworkError(ErrorCode.CAPABILITY_DENIED);
    }
    const contract = capabilityContracts[capability];
    if (contract) decode(contract[0], input);
    const attempts =
      !mutating && plugin.retry_safe.includes(capability)
        ? integer(this.application['max_attempts'])
        : 1;
    let timeout = options.timeout;
    if (/^(device|emulator)\./.test(capability) && (timeout === null || timeout === 0)) {
      const settings = object(this.application['device']);
      timeout =
        capability === 'device.install'
          ? integer(settings['install_timeout_seconds']) + 60
          : integer(settings['operation_timeout_seconds'] ?? 120);
    } else if (timeout === null) {
      timeout =
        capability === 'ai.decide'
          ? integer(object(this.application['codex'])['timeout_seconds']) + 45
          : integer(this.application['call_timeout_seconds']);
    }
    if (timeout < 0 || attempts < 1) throw new FrameworkError(ErrorCode.INVALID_CONTRACT);
    let retryOf: string | null = null;
    for (let attempt = 1; attempt <= attempts; attempt++) {
      this.check(options.signal);
      const span = options.span && attempt === 1 ? options.span : uid('span');
      const ids: TraceIds = {
        span_id: span,
        parent_span_id: options.parent,
        request_id: uid('req'),
      };
      const started = performance.now();
      const inputRef = this.artifact(`calls/${span}/input.json`, input);
      const payload = {
        capability,
        plugin: plugin.name,
        attempt,
        retry_of: retryOf,
        input_ref: inputRef,
        unavailable_optional_capabilities: plugin.optional_capabilities.filter(
          (item) => !this.providers.has(item),
        ),
      };
      this.record('call.started', payload, ids);
      try {
        const result = await this.attempt(
          plugin,
          capability,
          input,
          timeout,
          ids,
          options.ancestry,
          options.action,
          options.signal,
        );
        if (contract) decode(contract[1], result);
        const outputRef = this.artifact(`calls/${span}/output.json`, result);
        this.record(
          'call.completed',
          {
            capability,
            output_ref: outputRef,
            duration_ms: Math.round(performance.now() - started),
            attempt,
          },
          ids,
        );
        return result;
      } catch (error) {
        const failure = frameworkError(error);
        const incomplete =
          [
            ErrorCode.TIMEOUT,
            ErrorCode.PROCESS_EXITED,
            ErrorCode.CANCELLED,
            ErrorCode.DEVICE_INSTALL_UNKNOWN,
          ].includes(failure.code) ||
          (mutating && this.store.latestAction(this.runId)?.['status'] === 'unknown');
        const exception =
          error instanceof Error && !(error instanceof FrameworkError)
            ? { name: error.name, stack: error.stack ?? null }
            : null;
        this.record(
          incomplete ? 'call.incomplete' : 'call.failed',
          {
            capability,
            error: failure.details(),
            exception,
            attempt,
            duration_ms: Math.round(performance.now() - started),
          },
          ids,
        );
        if (attempt >= attempts || !failure.retryable) throw failure;
        retryOf = span;
        this.record('call.retry', { previous_span_id: span, next_attempt: attempt + 1 }, ids);
        await delay(50 * attempt);
      }
    }
    throw new FrameworkError(ErrorCode.CALL_FAILED);
  }

  private async attempt(
    plugin: Plugin,
    capability: string,
    input: Fields,
    timeout: number,
    ids: TraceIds,
    ancestry: string[],
    actionId: string | null,
    parentSignal: AbortSignal,
  ): Promise<unknown> {
    const controller = new AbortController();
    const abort = (): void => {
      controller.abort();
    };
    const inbound = async (method: string, payload: Fields): Promise<unknown> => {
      this.check(controller.signal);
      const acknowledgement = { persisted: true };
      if (method === 'host.events') {
        try {
          this.store.eventBatch(this.runId, payload['events'], ids);
        } catch (error) {
          if (!this.store.database.failure)
            this.record('rpc.batch.rejected', { submitted: payload }, ids);
          this.failure ??= frameworkError(error);
          throw error;
        }
        return acknowledgement;
      }
      if (method === 'host.action') {
        const session = object(input['session']);
        const expected = { instance_id: session['instance_id'], serial: session['serial'] };
        if (
          !['device.install', 'device.execute'].includes(capability) ||
          !actionId ||
          canonical(payload) !== canonical(expected)
        )
          throw new FrameworkError(ErrorCode.CAPABILITY_DENIED);
        this.store.command('dispatch_device_action', { run_id: this.runId, action_id: actionId });
        return acknowledgement;
      }
      if (method === 'host.event') {
        const kind = text(payload['kind']);
        if (!/^(provider|ai|plugin)\./.test(kind))
          throw new FrameworkError(ErrorCode.INVALID_EVENT);
        const fields = object(payload['payload'] ?? {});
        if (kind === 'ai.session') {
          this.store.command('save_session', {
            run_id: this.runId,
            role: fields['role'],
            thread_id: fields['thread_id'],
          });
        }
        try {
          this.store.event(this.runId, kind, fields, {
            ...ids,
            event_id: optionalText(payload['event_id']),
          });
        } catch (error) {
          this.failure ??= frameworkError(error);
          throw error;
        }
        return acknowledgement;
      }
      if (method === 'host.call') {
        const needed = text(payload['capability']);
        if (![...plugin.required_capabilities, ...plugin.optional_capabilities].includes(needed))
          throw new FrameworkError(ErrorCode.CAPABILITY_DENIED, 'Undeclared plugin dependency');
        const nested = object(payload['input']);
        let action: string | null = null;
        if (needed === 'emulator.activate') {
          const expected = { session: input['session'], package_name: input['package_name'] };
          if (
            capability !== 'device.execute' ||
            canonical(nested) !== canonical(expected) ||
            !['launch', 'return'].includes(text(object(input['action'])['type']))
          )
            throw new FrameworkError(ErrorCode.CAPABILITY_DENIED);
          action = actionId;
        }
        return this.call(needed, nested, {
          parent: ids.span_id ?? null,
          timeout: null,
          ancestry: [...ancestry, capability],
          span: null,
          action,
          signal: controller.signal,
        });
      }
      throw new FrameworkError(ErrorCode.METHOD_NOT_FOUND, method);
    };
    const observe = async (direction: 'in' | 'out', message: Fields): Promise<void> => {
      await Promise.resolve();
      const recorded: Fields = { message };
      if (direction === 'in' && message['method'] === 'host.events') {
        const entries = object(message['params'])['events'];
        if (
          Array.isArray(entries) &&
          entries.every(
            (entry: unknown) =>
              entry !== null &&
              typeof entry === 'object' &&
              !Array.isArray(entry) &&
              typeof object(entry)['event_id'] === 'string',
          )
        ) {
          const references = entries.map((entry: unknown) => ({
            event_id: text(object(entry)['event_id']),
          }));
          recorded['message'] = { ...message, params: { events: references } };
          recorded['event_payloads_referenced'] = true;
        }
      }
      this.record(`rpc.${direction}`, recorded, ids);
    };
    const command = plugin.command[0];
    if (!command) throw new FrameworkError(ErrorCode.INVALID_PLUGIN);
    const environment = { ...process.env, PYTHONUTF8: '1', PYTHONUNBUFFERED: '1' };
    const child = await start(command, plugin.command.slice(1), plugin.cwd, environment);
    parentSignal.addEventListener('abort', abort, { once: true });
    if (parentSignal.aborted) controller.abort();
    const peer = new Peer(child.stdout, child.stdin, inbound, observe, false, false);
    const lines = createInterface({ input: child.stderr });
    const stderr = (async () => {
      for await (const value of lines) {
        const line: unknown = value;
        this.record('plugin.stderr', { text: `${text(line)}\n` }, ids);
      }
    })();
    const execute = async (): Promise<unknown> => {
      const handshake = integer(this.application['call_timeout_seconds']) * 1000;
      const initialized = object(
        await peer.request(
          'initialize',
          { protocol_version: '1.0', features: ['event-batches-v1'] },
          handshake,
        ),
      );
      if (
        initialized['protocol_version'] !== '1.0' ||
        initialized['version'] !== plugin.version ||
        canonical(initialized['capabilities']) !== canonical(plugin.capabilities)
      )
        throw new FrameworkError(ErrorCode.PLUGIN_INCOMPATIBLE);
      if (canonical(await peer.request('health', {}, handshake)) !== canonical({ ok: true }))
        throw new FrameworkError(ErrorCode.PLUGIN_UNHEALTHY);
      const context = {
        task_id: this.store.run(this.runId)['task_id'],
        run_id: this.runId,
        ...ids,
      };
      this.check(controller.signal);
      return peer.request('invoke', { capability, input: redact(input), context }, timeout * 1000);
    };
    let interval: NodeJS.Timeout | undefined;
    let deadline: NodeJS.Timeout | undefined;
    const cancelled = new Promise<never>((_resolve, reject) => {
      interval = setInterval(() => {
        try {
          this.check(controller.signal);
        } catch (error) {
          reject(frameworkError(error));
        }
      }, 100);
      if (timeout > 0)
        deadline = setTimeout(
          () =>
            reject(new FrameworkError(ErrorCode.TIMEOUT, 'Plugin call deadline exceeded', true)),
          timeout * 1000,
        );
    });
    const invocation = execute();
    const completed = invocation.then((value) => {
      const result = { completed: true, value };
      return result;
    });
    const diagnosticsEnded = stderr.then(() => {
      const result = { completed: false, value: null };
      return result;
    });
    try {
      const result = await Promise.race([completed, diagnosticsEnded, cancelled]);
      if (result.completed) return result.value;
      return await Promise.race([invocation, cancelled]);
    } finally {
      clearInterval(interval);
      clearTimeout(deadline);
      controller.abort();
      parentSignal.removeEventListener('abort', abort);
      lines.close();
      // Close protocol handlers before killing the child; no new action can be acknowledged.
      try {
        await peer.close();
      } finally {
        try {
          await terminate(child);
        } finally {
          await Promise.allSettled([invocation, stderr]);
        }
      }
    }
  }
}
