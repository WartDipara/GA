import { checkpointHash } from '../utilities/hashing.js';
import { object, text } from '@game-automation/contracts';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import { integer } from '../utilities/values.js';
import { Battle } from './battle.js';
import { Exploration } from '../exploration/controller.js';
import { Knowledge } from './knowledge.js';
import { executeRoutine, Perception, route, verifiesLocalOutcome, type View } from './local.js';
import type { AgentDirectory } from './agent-directory.js';
import { TestAgents, type Assessment } from './agents.js';
import { PassiveWait } from './passive-wait.js';
import { readSettlement, settleAction } from './settlement.js';
import { Watchdog } from './watchdog.js';
import type { Runtime } from './runtime.js';
import type { Decision, Observation, Pending } from './types.js';

const maximumWorkflowCycles = 1000;

async function complete(
  runtime: Runtime,
  agents: TestAgents,
  decision: Decision,
  view: View,
  battle: Battle | null,
): Promise<void> {
  const observation = view.observation;
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
  const goal = text(runtime.task['goal']);
  const interaction = progress.paths.findLast((path) => path.status === 'verified' && path.type === 'tap');
  const advancement = battle?.data.advances.findLast((item) => item.run_id === runtime.runId);
  const historical = advancement
    ? [advancement.settlement.observation_id, advancement.observation_id]
    : [interaction?.before, interaction?.after].filter((id): id is string => Boolean(id));
  const acceptance = await agents.judge(view, `Verify the recorded acceptance conditions for ${goal}.`, {
    goal,
    conditions: goal === 'explore'
      ? 'The reachable frontier is settled, visible viewports were scanned and the root was rechecked.'
      : goal === 'advance-battle'
        ? 'A victory settlement is followed by a different stage marker in this execution.'
        : 'The full bound identity reached gameplay and one benign interaction was verified.',
    identity: progress.identity,
    verified_paths: progress.paths.filter((path) => path.status === 'verified').slice(-5),
    battle: battle?.data ?? null,
    exploration: agents.catalog?.context() ?? null,
  }, null, historical);
  if (acceptance.status !== 'verified')
    throw new FrameworkError(ErrorCode.GAMEPLAY_BLOCKED, acceptance.reason);
  runtime.record('gameplay.goal.completed', {
    goal: runtime.task['goal'],
    observation_id: observation.observation_id,
    source: agents.directory.agent('Evidence Judge').id,
    evidence_refs: acceptance.evidence_refs,
    identity: progress.identity,
    objective: decision.objective,
  });
}

function actionAttempt(runtime: Runtime, view: View, decision: Decision): string | null {
  const action = decision.action;
  if (!action || (runtime.task['goal'] === 'explore' && ['back', 'swipe'].includes(action.type)))
    return null;
  const { progress, task } = runtime;
  const target = view.facts.candidates.find(({ bounds }) =>
    action.x >= bounds[0] && action.x < bounds[2] && action.y >= bounds[1] && action.y < bounds[3]);
  const element = decision.exploration?.elements.find(({ bounds }) =>
    action.x >= bounds[0] && action.x < bounds[2] && action.y >= bounds[1] && action.y < bounds[3]);
  const position = [Math.floor(action.x / 32), Math.floor(action.y / 32)];
  const controls = view.facts.candidates.map((item) => item.resource_id || item.class_name).sort();
  const page = decision.exploration?.page_key ?? decision.state_key ?? 'unknown';
  const subject = element ? [element.container, element.item, element.key] : target?.target_ref ?? position;
  return checkpointHash([
    task['goal'], progress.data.identity, page, controls, progress.data.battle?.stage ?? null,
    action.type, action.credential_field, subject,
  ]);
}

async function updateIdentity(
  runtime: Runtime, agents: TestAgents, decision: Decision, view: View,
): Promise<void> {
  if (!decision.identity) return;
  const progress = runtime.progress.data;
  const identity = { ...progress.identity };
  const keys: (keyof typeof decision.identity)[] = ['small_account', 'server', 'role'];
  const changes: Record<string, string> = {};
  for (const key of keys) {
    const value = decision.identity[key];
    if (value === null) continue;
    const known = identity[key];
    if (known && known !== value)
      throw new FrameworkError(ErrorCode.GAMEPLAY_BLOCKED, 'Observed identity differs from the checkpoint');
    if (!known) changes[key] = value;
  }
  if (!Object.keys(changes).length) return;
  const verdict = await agents.judge(view, 'Verify the candidate identity fields visible in this observation.', {
    candidate_identity: changes,
    previously_bound_identity: progress.identity,
  });
  if (verdict.status !== 'verified')
    throw new FrameworkError(ErrorCode.GAMEPLAY_BLOCKED, verdict.reason);
  const bound = { small_account: null, server: null, role: null, ...identity, ...changes };
  runtime.store.database.transaction(() => {
    progress.identity = bound;
    runtime.record('gameplay.identity.verified', { identity: bound, ...verdict });
    runtime.progress.save();
  });
}

export async function runTestWorkflow(
  runtime: Runtime, initial: Observation, directory: AgentDirectory,
): Promise<void> {
  const perception = new Perception(runtime);
  const passive = new PassiveWait(runtime, perception);
  const exploration = runtime.task['goal'] === 'explore' ? new Exploration(runtime) : null;
  const catalog = exploration?.catalog ?? null;
  catalog?.reconcileLedger();
  const knowledge = new Knowledge(runtime);
  const battle = runtime.task['goal'] === 'advance-battle' ? new Battle(runtime) : null;
  const progress = runtime.progress.data;
  const watchdog = new Watchdog(
    runtime,
    object(object(runtime.manifest['application'])['gameplay']),
    () => exploration?.redirectStall() ?? false,
  );
  runtime.watchdog = watchdog;
  const agents = new TestAgents(runtime, watchdog, catalog, directory);
  const judgeId = directory.agent('Evidence Judge').id;
  const leadId = directory.agent('Test Lead').id;
  let view = await perception.settle(initial);
  let cachedSteps = 0;
  let cycles = 0;
  for (;;) {
    await runtime.admit();
    cycles += 1;
    if (cycles >= maximumWorkflowCycles && !progress.pending)
      throw new FrameworkError(ErrorCode.BUDGET_EXHAUSTED, 'Workflow cycle limit reached; resume to continue');
    const pending = progress.pending;
    const recorded = pending ? readSettlement(runtime, pending) : null;
    if (exploration && pending && !recorded && (await exploration.verifyKnown(view))) continue;
    const limit = integer(runtime.task['max_turns'] ?? 0);
    if (limit && agents.turns >= limit && !progress.pending)
      throw new FrameworkError(
        ErrorCode.BUDGET_EXHAUSTED,
        'Requested planning-turn boundary reached; resume to continue',
      );
    const passiveState = progress.update_wait || progress.network_wait;
    if (exploration && !progress.pending && !passiveState && !watchdog.reconciling && cachedSteps < 5) {
      try {
        const before = await exploration.attempt(view);
        if (before) {
          cachedSteps += 1;
          view = await perception.settle(before);
          continue;
        }
      } catch (error) {
        if (
          !(error instanceof FrameworkError) ||
          error.code !== ErrorCode.STALE_OBSERVATION ||
          progress.pending
        )
          throw error;
        runtime.record('exploration.location.rejected', { reason: 'fresh-target-changed' });
        view = await perception.settle(view.observation);
      }
    }
    if (!catalog && !progress.pending && !passiveState && !watchdog.reconciling && cachedSteps < 5) {
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
    runtime.begin(selection.reason);
    let assessment: Assessment | null = recorded;
    let source = judgeId;
    if (pending && !recorded) {
      const execution = pending.action_id ? runtime.store.deviceAction(pending.run_id, pending.action_id) : null;
      if (!execution || !['executed', 'unknown'].includes(text(execution['status'])))
        throw new FrameworkError(ErrorCode.ACTION_ALREADY_DISPATCHED, 'Pending action and ledger disagree');
      if (execution['status'] === 'executed' &&
          view.observation.runtime.focused_display?.package_name === runtime.task['target'] &&
          verifiesLocalOutcome(pending, view)) {
        source = 'local-predicates';
        assessment = {
          status: 'verified', reason: 'Stable local exit predicates matched',
          evidence_refs: [view.observation.observation_id], missing_evidence: [],
        };
      } else {
        assessment = await agents.judge(view, 'Determine the outcome of the pending device action.', {
          execution: {
            action_id: execution['action_id'], status: execution['status'], result: execution['result'],
          },
          expected_outcome: pending.expected_outcome ?? null,
          before_observation: pending.observation_id,
          current_observation: view.observation.observation_id,
          device_runtime: view.observation.runtime,
        }, pending);
      }
      if (assessment.status === 'unknown') {
        runtime.record('gameplay.outcome.unknown', { action_id: pending.action_id, ...assessment });
        watchdog.assistance();
        const observationOnly = await agents.decide(
          view, 'pending-outcome-observation-only', assessment, 0, pending,
        );
        const refreshed = await passive.evaluate(observationOnly, view, agents);
        view = refreshed ?? await perception.wait(view.observation);
        continue;
      }
      settleAction(runtime, pending, view, assessment, source);
    }
    if (pending && recorded) {
      catalog?.reconcileLedger();
      runtime.record('gameplay.outcome.reused', { action_id: pending.action_id, ...recorded });
    }
    const decision = await agents.decide(view, selection.reason, assessment, pending ? 0 : limit, pending);
    const projection = exploration && !recorded && pending?.exploration ? pending : null;
    if (pending && !projection) {
      if (assessment?.status === 'verified' && !catalog && !recorded)
        knowledge.learn(pending, view.observation, view.facts);
      progress.pending = null;
      runtime.progress.save();
    }
    const refreshed = await passive.evaluate(decision, view, agents);
    if (refreshed) {
      view = refreshed;
      continue;
    }
    cachedSteps = 0;
    const requestedWait = decision.disposition === 'wait' || decision.action?.type === 'wait';
    const page = exploration ? await exploration.review(decision, view, projection) : null;
    if (exploration && projection && assessment) {
      runtime.store.database.transaction(() => {
        exploration.outcome(decision, view, projection, page, assessment.status === 'verified');
        progress.pending = null;
        runtime.progress.save();
      });
    }
    watchdog.review(decision, view.observation, pending, assessment);
    await updateIdentity(runtime, agents, decision, view);
    runtime.progress.update(decision, view.observation.observation_id);
    const phase = decision.battle_progress;
    if (battle && phase && (phase.phase !== battle.data.phase ||
        (phase.stage && phase.stage !== battle.data.stage) ||
        (phase.settlement_result === 'victory' && !battle.data.settlement))) {
      const verified = await agents.judge(view, 'Verify the visible battle phase, stage and settlement claims.', {
        candidate: phase, previous_state: battle.data,
      });
      if (verified.status !== 'verified') {
        watchdog.withoutDispatch(view.observation, 'unverified-battle-state');
        view = await perception.wait(view.observation);
        continue;
      }
      battle.update(phase, view.observation, judgeId);
    }
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
    ) {
      if (exploration && page) {
        runtime.record('exploration.branch.blocked', {
          observation_id: view.observation.observation_id,
          page_id: page.id,
          reason: decision.reason_summary,
          next: exploration.hint(),
        });
        if (!exploration.hint() && exploration.complete(page)) {
          await complete(runtime, agents, decision, view, battle);
          return;
        }
        if (await exploration.redirect(decision, page, view.observation, 'lead-branch-blocked'))
          continue;
        if (exploration.hint()) {
          watchdog.withoutDispatch(
            view.observation,
            'lead-ignores-alternative-frontier',
            page.id,
          );
          continue;
        }
      }
      throw new FrameworkError(ErrorCode.GAMEPLAY_BLOCKED, decision.reason_summary);
    }
    if (decision.disposition === 'complete') {
      if (exploration && !exploration.complete(page)) {
        runtime.record('exploration.completion.deferred', {
          reason: 'Frontier, viewport scans or root recheck remain',
          next: exploration.hint(),
        });
        watchdog.withoutDispatch(view.observation, 'unsettled-exploration-frontier', page?.id);
        continue;
      }
      await complete(runtime, agents, decision, view, battle);
      return;
    }
    if (cycles >= maximumWorkflowCycles)
      throw new FrameworkError(ErrorCode.BUDGET_EXHAUSTED, 'Workflow cycle limit reached; resume to continue');
    if (limit && agents.turns >= limit)
      throw new FrameworkError(
        ErrorCode.BUDGET_EXHAUSTED,
        'Planning boundary reached before dispatching another action',
      );
    if (requestedWait) {
      view = await perception.wait(view.observation, battle?.data.phase === 'active' ? 30 : 0);
      continue;
    }
    if (!decision.action) throw new FrameworkError(ErrorCode.AI_OUTPUT_INVALID);
    if (decision.action.type === 'credential' && !decision.sensitive_regions.length)
      throw new FrameworkError(ErrorCode.CAPABILITY_DENIED);
    if (exploration && !exploration.permit(decision.action, page)) {
      runtime.record('exploration.proposal.rejected', {
        workspace_key: catalog?.key,
        observation_id: view.observation.observation_id,
        dispatched: false,
        reason: 'Target is absent, settled, locked or prohibited',
        required_correction:
          'Enumerate the proposed visible target in this viewport inventory; exclude covered controls. No device input occurred for this proposal.',
        next: exploration.hint(),
      });
      if (
        await exploration.redirect(
          decision,
          page,
          view.observation,
          'rejected-exploration-proposal',
        )
      )
        continue;
      watchdog.withoutDispatch(view.observation, 'rejected-exploration-proposal', page?.id);
      continue;
    }
    const action = decision.action;
    const testAttempt = actionAttempt(runtime, view, decision);
    const attempts = progress.test_attempts ?? {};
    if (testAttempt && (attempts[testAttempt] || Object.keys(attempts).length >= 2000)) {
      watchdog.withoutDispatch(view.observation, 'settled-action-or-attempt-limit', testAttempt);
      continue;
    }
    let actionId: string;
    try {
      const link = exploration ? await exploration.intent(action, page, view.observation) : null;
      const intent: Partial<Pending> = {
        expected_outcome: decision.expected_outcome,
        local_after: decision.local_after,
        test_objective: decision.objective,
        screen_key: view.facts.screen_key,
        risk: decision.risk,
        cache_scope: knowledge.scope(view.observation),
        cache_intent: knowledge.intent(),
        image_sha256: view.observation.screenshot.sha256,
        sensitive_regions: view.observation.sensitive_regions,
        ...(testAttempt ? { test_attempt: testAttempt } : {}),
      };
      actionId = await runtime.perform(
        action, view.observation, leadId, null, null, link, intent,
      );
    } catch (error) {
      if (!(error instanceof FrameworkError) || error.code !== ErrorCode.STALE_OBSERVATION || progress.pending)
        throw error;
      runtime.record('gameplay.correction', { reason: 'stale-target-not-executed' });
      watchdog.withoutDispatch(view.observation, 'stale-target-not-executed');
      view = await perception.settle(view.observation);
      continue;
    }
    const dispatched = progress.pending;
    if (!dispatched) throw new FrameworkError(ErrorCode.INVALID_PROTOCOL);
    if (page) await exploration?.afterDispatch(dispatched, actionId, page, view.observation);
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
