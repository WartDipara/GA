import { object, text } from '@game-automation/contracts';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import { integer } from '../storage/values.js';
import { Battle } from './battle.js';
import { Catalog } from '../exploration/catalog.js';
import { Knowledge } from './knowledge.js';
import { executeRoutine, Perception, route } from './local.js';
import { Planner } from './planning.js';
import { Watchdog } from './watchdog.js';
import type { Runtime } from './runtime.js';
import type { Decision, Observation } from './types.js';

function complete(
  runtime: Runtime,
  decision: Decision,
  observation: Observation,
  battle: Battle | null,
): void {
  const progress = runtime.progress.data;
  if (battle && !battle.verified())
    throw new FrameworkError(
      ErrorCode.GAMEPLAY_BLOCKED,
      'Battle completion requires settlement and a new stage marker',
    );
  if (
    !progress.identity ||
    !progress.paths.some((path) => path.status === 'verified' && path.type === 'tap')
  )
    throw new FrameworkError(
      ErrorCode.GAMEPLAY_BLOCKED,
      'Completion requires a bound identity and verified interaction',
    );
  if (
    runtime.task['goal'] === 'login-and-interact' &&
    (!progress.identity.small_account || !progress.identity.server || !progress.identity.role)
  )
    throw new FrameworkError(
      ErrorCode.GAMEPLAY_BLOCKED,
      'Login completion requires the full observed identity',
    );
  runtime.record('gameplay.goal.completed', {
    goal: runtime.task['goal'],
    observation_id: observation.observation_id,
    source: 'model-semantic-review',
    identity: progress.identity,
    objective: decision.objective,
  });
}

export async function runLoop(runtime: Runtime, initial: Observation): Promise<void> {
  const perception = new Perception(runtime);
  const catalog = runtime.task['goal'] === 'explore' ? new Catalog(runtime) : null;
  const planner = new Planner(runtime, catalog);
  const knowledge = new Knowledge(runtime);
  const battle = runtime.task['goal'] === 'advance-battle' ? new Battle(runtime) : null;
  const progress = runtime.progress.data;
  const watchdog = new Watchdog(
    runtime,
    object(object(runtime.manifest['application'])['gameplay']),
  );
  runtime.watchdog = watchdog;
  let view = await perception.settle(initial);
  let cachedSteps = 0;
  for (;;) {
    await runtime.admit();
    const limit = integer(runtime.task['max_turns'] ?? 0);
    if (limit && planner.turns >= limit)
      throw new FrameworkError(
        ErrorCode.BUDGET_EXHAUSTED,
        'Requested planning-turn boundary reached; resume to continue',
      );
    if (!progress.pending && !watchdog.reconciling && cachedSteps < 5) {
      const steps = knowledge.routine(view.observation, view.facts).slice(0, 5 - cachedSteps);
      if (steps.length) {
        runtime.record('gameplay.cache.hit', { steps: steps.length });
        try {
          view = await executeRoutine(runtime, view.observation, steps, view.facts);
          cachedSteps += steps.length;
        } catch (error) {
          if (
            !(error instanceof FrameworkError) ||
            ![ErrorCode.GAMEPLAY_BLOCKED, ErrorCode.STALE_OBSERVATION].includes(error.code)
          )
            throw error;
          runtime.record('gameplay.cache.invalidated', { reason: error.code });
          knowledge.entries.length = 0;
          // Retain the unresolved pending action for semantic review before another dispatch.
          view = await perception.settle(view.observation);
        }
        continue;
      }
    }
    const selection = route(view);
    if (watchdog.data.rounds >= 3) selection.reason = 'no-verified-progress';
    runtime.record('gameplay.route', selection);
    const decision = await planner.decide(view.observation, view.facts, selection.reason);
    cachedSteps = 0;
    const pending = progress.pending;
    const requestedWait = decision.disposition === 'wait' || decision.action?.type === 'wait';
    if (pending) {
      const assessment = decision.previous_outcome;
      if (assessment.status === 'unknown' && requestedWait) {
        watchdog.review(decision, view.observation, pending);
        view = await perception.wait(view.observation);
        continue;
      }
      if (
        !['verified', 'deviated'].includes(assessment.status) ||
        !assessment.evidence_refs.includes(view.observation.observation_id)
      )
        throw new FrameworkError(
          ErrorCode.GAMEPLAY_BLOCKED,
          'Previous action outcome remains unverified; no action replay',
        );
      const outcome = {
        source: 'model-semantic-review',
        observation_run: runtime.runId,
        observation_id: view.observation.observation_id,
        ...assessment,
      };
      runtime.outcome(
        pending.run_id,
        text(pending.action_id),
        assessment.status === 'verified' ? 'verified' : 'reconciled',
        outcome,
      );
      runtime.record('gameplay.outcome', outcome);
      for (const path of progress.paths)
        if (path.action_id === pending.action_id) {
          path.status = assessment.status;
          path.after = view.observation.observation_id;
        }
      if (assessment.status === 'verified') knowledge.learn(pending, view.observation, view.facts);
      progress.pending = null;
      runtime.progress.save();
    }
    if (decision.identity) {
      const identity = progress.identity ?? { small_account: null, server: null, role: null };
      const keys: (keyof typeof identity)[] = ['small_account', 'server', 'role'];
      for (const key of keys) {
        const value = decision.identity[key];
        if (value === null) continue;
        if (identity[key] !== null && identity[key] !== value)
          throw new FrameworkError(
            ErrorCode.GAMEPLAY_BLOCKED,
            'Observed identity differs from the checkpoint',
          );
        identity[key] = value;
      }
      progress.identity =
        identity.small_account || identity.server || identity.role ? identity : null;
    }
    const page = decision.exploration ? catalog?.ingest(decision.exploration, view.observation) : null;
    if (pending) catalog?.outcome(
      pending, page ?? null, view.observation, decision.previous_outcome.status === 'verified',
    );
    // Commit known outcomes and their catalog evidence before the watchdog can stop dispatch.
    watchdog.review(decision, view.observation, pending);
    runtime.progress.update(decision, view.observation.observation_id);
    battle?.update(decision.battle_progress, view.observation);
    progress.checkpoint = {
      run_id: runtime.runId,
      observation_id: view.observation.observation_id,
      runtime: view.observation.runtime,
      objective: decision.objective,
      screen_key: view.facts.screen_key,
    };
    progress.sensitive_regions = decision.sensitive_regions;
    runtime.progress.save();
    if (
      decision.disposition === 'blocked' ||
      ['real_payment', 'account_delete', 'unknown'].includes(decision.risk)
    )
      throw new FrameworkError(ErrorCode.GAMEPLAY_BLOCKED, decision.reason_summary);
    if (decision.disposition === 'complete') {
      complete(runtime, decision, view.observation, battle);
      return;
    }
    if (!decision.action) throw new FrameworkError(ErrorCode.AI_OUTPUT_INVALID);
    if (limit && planner.turns >= limit)
      throw new FrameworkError(
        ErrorCode.BUDGET_EXHAUSTED,
        'Planning boundary reached before dispatching another action',
      );
    if (requestedWait) {
      view = await perception.wait(view.observation, battle?.data.phase === 'active' ? 30 : 0);
      continue;
    }
    if (decision.action.type === 'credential' && !decision.sensitive_regions.length)
      throw new FrameworkError(ErrorCode.CAPABILITY_DENIED);
    let actionId: string;
    try {
      actionId = await runtime.perform(decision.action, view.observation, 'planner');
      progress.planner_coordinate_action_dispatched = true;
      planner.mappingUntried = false;
    } catch (error) {
      if (!(error instanceof FrameworkError) || error.code !== ErrorCode.STALE_OBSERVATION)
        throw error;
      if (progress.pending) throw error;
      runtime.record('gameplay.correction', { reason: 'stale-target-not-executed' });
      view = await perception.settle(view.observation);
      continue;
    }
    const dispatched = progress.pending;
    if (!dispatched) throw new FrameworkError(ErrorCode.INVALID_PROTOCOL);
    Object.assign(dispatched, {
      expected_outcome: decision.expected_outcome,
      image_ref: view.observation.evidence_ref,
      screen_key: view.facts.screen_key,
      action_id: actionId,
      risk: decision.risk,
      cache_scope: knowledge.scope(view.observation),
      cache_intent: knowledge.intent(),
      image_sha256: view.observation.screenshot.sha256,
      sensitive_regions: view.observation.sensitive_regions,
    });
    if (page) catalog?.associate(dispatched, actionId, page);
    progress.paths.push({
      run_id: runtime.runId,
      action_id: actionId,
      before: view.observation.observation_id,
      objective: decision.objective,
      status: 'attempted',
      type: decision.action.type,
    });
    runtime.progress.save();
    view = await perception.settle(view.observation);
  }
}
