import { performance } from 'node:perf_hooks';
import { object, text } from '@game-automation/contracts';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import { canonical, strings, uid, type Fields } from '../storage/values.js';
import { buildWorkflow, type Stage } from './assembly.js';
import type { Execution } from './execution.js';

interface Context {
  outputs: Fields;
  apkIndex: number | null;
}

export class Scheduler {
  private readonly children: Map<string, string>;
  private readonly previousChildren = new Map<string, string>();
  private readonly completed = new Set<string>();

  constructor(
    private readonly execution: Execution,
    readonly runId: string,
    private readonly task: Fields,
    private readonly sourceFile: string | null,
  ) {
    const store = execution.store;
    this.children = this.stageChildren(runId);
    const continuation = store.events(runId, { kinds: ['run.continued_from'], limit: 1 }).at(-1);
    if (continuation) {
      const previous = text(continuation.payload['run_id']);
      const old = store.run(previous);
      if (
        old['mode'] !== 'workflow' ||
        old['task_id'] !== store.run(runId)['task_id'] ||
        canonical(store.input(previous)) !== canonical(task)
      )
        throw new FrameworkError(ErrorCode.WORKFLOW_INVALID, 'Continuation input differs');
      this.previousChildren = this.stageChildren(previous);
    }
    for (const event of store.events(runId, { kinds: ['workflow.stage.completed'] }))
      this.completed.add(text(event.payload['key']));
    for (const event of store.events(runId, { kinds: ['workflow.stage.started'] })) {
      const span = event.span_id;
      if (!span) continue;
      const closed = store.events(runId, {
        span,
        kinds: ['workflow.stage.completed', 'workflow.stage.failed', 'workflow.stage.incomplete'],
      });
      if (!closed.length)
        store.event(
          runId,
          'workflow.stage.incomplete',
          { ...event.payload, status: 'unknown', duration_ms: null },
          { span_id: span, parent_span_id: event.parent_span_id, request_id: event.request_id },
        );
    }
  }

  private stageChildren(runId: string): Map<string, string> {
    const store = this.execution.store;
    const children = new Map<string, string>();
    for (const run of store.taskRuns(text(store.run(runId)['task_id']))) {
      const id = text(run['id']);
      const input = store.input(id);
      if (input['workflow_run'] !== runId) continue;
      const key = text(input['workflow_key']);
      if (children.has(key))
        throw new FrameworkError(ErrorCode.WORKFLOW_INVALID, 'Duplicate stage execution');
      children.set(key, id);
    }
    return children;
  }

  private async step(stage: Stage, context: Context): Promise<void> {
    const { store } = this.execution;
    this.execution.check(this.runId);
    const key = stage.perApk ? `apk:${String(context.apkIndex)}:${stage.name}` : stage.name;
    let child = this.children.get(key) ?? null;
    if (this.completed.has(key)) {
      context.outputs[stage.name] = child;
      return;
    }
    const span = uid('span');
    const payload: Fields = {
      key,
      stage: stage.name,
      capability: `workflow.${stage.name}`,
      apk_index: context.apkIndex,
      status: 'running',
    };
    store.command('transition', { run_id: this.runId, status: 'running', stage: stage.name });
    store.event(this.runId, 'workflow.stage.started', payload, { span_id: span });
    const started = performance.now();
    let recovered = false;
    try {
      if (stage.mode === null)
        store.event(this.runId, 'workflow.report.requested', {
          publication: 'after_business_outcome',
        });
      else {
        const previous = this.previousChildren.get(key);
        if (!child && previous) {
          const old = store.run(previous);
          if (old['mode'] !== stage.mode)
            throw new FrameworkError(ErrorCode.WORKFLOW_INVALID, 'Stage mode changed');
          if (old['status'] === 'succeeded') {
            child = previous;
            this.children.set(key, child);
            payload['reused_from_previous_batch'] = true;
          }
        }
        if (!child) {
          const args = {
            task: this.task,
            stage: stage.name,
            outputs: context.outputs,
            apk_index: context.apkIndex,
          };
          const input = object(await this.execution.tool('workflow_input', this.runId, args));
          input['workflow_run'] = this.runId;
          input['workflow_key'] = key;
          child = await this.execution.create(
            text(store.run(this.runId)['task_id']),
            stage.mode,
            input,
            this.runId,
          );
          this.children.set(key, child);
          if (previous) store.event(child, 'run.continued_from', { run_id: previous });
        }
        payload['child_run'] = child;
        store.event(this.runId, 'workflow.stage.bound', payload, { span_id: span });
        let result = store.run(child);
        recovered = result['status'] === 'succeeded';
        if (!['succeeded', 'failed', 'cancelled'].includes(text(result['status'])))
          result = await this.execution.run(child, this.sourceFile);
        this.execution.check(this.runId);
        if (result['status'] !== 'succeeded') {
          payload['reason'] =
            store.events(child, { kinds: ['run.state'], limit: 1 }).at(-1)?.payload['reason'] ??
            null;
          throw new FrameworkError(
            ErrorCode.WORKFLOW_BLOCKED,
            `Stage ${stage.name} for APK ${String(context.apkIndex)}: ${child} is ${text(result['status'])}; inspect the child and resume the workflow`,
          );
        }
      }
      payload['duration_ms'] = recovered ? null : Math.round(performance.now() - started);
      if (recovered) payload['recovered'] = true;
      payload['status'] = 'succeeded';
      store.event(this.runId, 'workflow.stage.completed', payload, { span_id: span });
      this.completed.add(key);
      context.outputs[stage.name] = child;
    } catch (error) {
      payload['duration_ms'] = Math.round(performance.now() - started);
      payload['status'] = child ? store.run(child)['status'] : 'incomplete';
      store.event(this.runId, 'workflow.stage.failed', payload, { span_id: span });
      throw error;
    }
  }

  async run(): Promise<void> {
    const { task, stages } = buildWorkflow(this.task);
    await this.execution.tool('workflow_validate', this.runId, { task });
    const context: Context = { outputs: {}, apkIndex: null };
    const manifest = await this.execution.manifest(this.runId);
    const providers = Object.values(object(manifest['plugins'])).flatMap((plugin) =>
      strings(object(plugin)['capabilities']),
    );
    for (const stage of stages)
      for (const capability of stage.capabilities) {
        if (!providers.includes(capability))
          throw new FrameworkError(ErrorCode.MISSING_CAPABILITY, capability);
      }
    const plan = { stages: task.stages, instances: task.instances, order: 'apk-list' };
    this.execution.store.event(this.runId, 'workflow.plan', plan);
    let index = 0;
    while (index < stages.length) {
      const stage = stages[index];
      if (!stage) throw new FrameworkError(ErrorCode.WORKFLOW_INVALID);
      if (!stage.perApk) {
        await this.step(stage, context);
        index++;
        continue;
      }
      let end = index;
      while (stages[end]?.perApk) end++;
      for (let apkIndex = 0; apkIndex < task.source_ids.length; apkIndex++) {
        const item: Context = { outputs: { ...context.outputs }, apkIndex };
        for (const next of stages.slice(index, end)) await this.step(next, item);
      }
      index = end;
    }
  }
}
