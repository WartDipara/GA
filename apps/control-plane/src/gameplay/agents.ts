import { checkpointHash, sha256 } from '../utilities/hashing.js';
import { existsSync } from 'node:fs';
import { mkdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { performance } from 'node:perf_hooks';
import { decode, object, schema, text, type ContractMap } from '@game-automation/contracts';
import { ErrorCode, FrameworkError, frameworkError } from '@game-automation/contracts/errors';
import { redact } from '../utilities/redaction.js';
import { strings, uid, type Fields } from '../utilities/values.js';
import { within } from '../utilities/files.js';
import type { Catalog } from '../exploration/catalog.js';
import { type AgentDirectory, type GameplayAgent, type GameplayJob } from './agent-directory.js';
import { rectangle, projectAction } from './coordinates.js';
import { verifyPlanningDependencies } from './dependencies.js';
import { Handoffs, type ContextRequest } from './handoff.js';
import type { View } from './local.js';
import type { Runtime } from './runtime.js';
import type { Coordinates, Decision, Observation, Pending } from './types.js';
import type { Watchdog } from './watchdog.js';

type Verdict = ContractMap['test-evidence-verdict'];
export type Assessment = Pick<Verdict, 'reason' | 'evidence_refs' | 'missing_evidence'> & {
  status: 'verified' | 'deviated' | 'unknown';
};
type Delegation = NonNullable<Decision['delegation']>;
type Skill = NonNullable<Decision['skill']>;
type Output = 'test-lead-decision' | 'test-evidence-verdict' | 'test-worker-result';
type Image = { id: string; path: string; coordinates: Coordinates };

const workerRoutes: Record<Delegation['specialty'], { job: GameplayJob; skill: string }> = {
  interface: { job: 'Interface Specialist', skill: 'test-interface' },
  exploration: { job: 'Exploration Specialist', skill: 'test-frontier' },
  comparison: { job: 'Comparison Specialist', skill: 'test-comparison' },
};
const triggers: Record<Skill, string> = {
  'game-entry': 'Identify sign-in, account, server and role. Deliver an entry action or blocker.',
  'game-exploration': 'Inspect a reachable viewport. Deliver stable page and element candidates.',
  'game-battle': 'Observe and advance native battle. Deliver phase and stage evidence.',
  'game-recovery': 'Reconcile a lost session or interrupted flow. Deliver a bounded recovery proposal.',
};

export class TestAgents {
  turns = 0;
  private leadCalls = 0;
  private readonly handoffs: Handoffs;
  private readonly codex: Fields;
  private readonly active: Set<Skill>;
  private readonly images = new Map<string, Image>();

  constructor(
    readonly runtime: Runtime,
    readonly watchdog: Watchdog,
    readonly catalog: Catalog | null,
    readonly directory: AgentDirectory,
  ) {
    const application = object(runtime.manifest['application']);
    const codex = object(application['codex']);
    this.codex = {
      executable: codex['executable'],
      version: codex['version'],
      timeout_seconds: codex['timeout_seconds'],
    };
    this.handoffs = new Handoffs(runtime);
    this.active = new Set();
  }

  private async image(runId: string, id: string, reference: string): Promise<Image> {
    const key = `${runId}-${id}`;
    const cached = this.images.get(key);
    if (cached) return cached;
    const { runtime } = this;
    if (runtime.store.run(runId)['task_id'] !== this.handoffs.taskId ||
        !/^(obs_[a-f0-9]+|interrupted-observation)$/.test(id))
      throw new FrameworkError(ErrorCode.INVALID_PATH);
    const source = within(runtime.store.runDirectory(runId), reference);
    const path = within(runtime.directory, `artifacts/agent-images/${key}.png`);
    const coordinates = decode('planner-image-result', await runtime.call('planner.image', {
      source,
      destination: path,
    }));
    const image = { id, path, coordinates };
    if (this.images.size >= 8) this.images.clear();
    this.images.set(key, image);
    return image;
  }

  private async call<K extends Output>(
    agent: GameplayAgent,
    output: K,
    stage: string,
    skills: string[],
    context: Fields,
    images: Image[],
    observation: Observation,
    question: string,
    parent: string | null = this.runtime.step,
  ): Promise<{ response: ContractMap[K]; span: string }> {
    const { runtime } = this;
    const span = uid('span');
    const start = performance.now();
    const { id: role, job_title: jobTitle, describ } = agent;
    const metadata = { role, job_title: jobTitle, question, observation_id: observation.observation_id };
    runtime.record('gameplay.agent.started', metadata, span, parent);
    try {
      await verifyPlanningDependencies(runtime);
      const config = object(runtime.manifest['skills_config']);
      const allowed = strings(object(config['stages'])[stage]);
      const base = text(config['base']);
      const root = text(object(runtime.manifest['resource_roots'])['skills']);
      const hashes = object(runtime.manifest['files']);
      const workspace = join(runtime.directory, 'ai-workspace', role);
      await mkdir(workspace, { recursive: true });
      const input: Fields[] = [];
      const instructions: string[] = [];
      const loaded = stage === 'test_workers' ? skills : [base, ...skills];
      for (const name of loaded) {
        if (name !== base && !allowed.includes(name))
          throw new FrameworkError(ErrorCode.CAPABILITY_DENIED);
        const path = within(root, `${name}/SKILL.md`);
        const bytes = await readFile(path);
        if (sha256(bytes) !== hashes[`skills/${name}/SKILL.md`])
          throw new FrameworkError(ErrorCode.SNAPSHOT_CHANGED);
        instructions.push(`<runtime_skill name="${name}">\n${bytes.toString('utf8')}\n</runtime_skill>`);
        input.push({ type: 'skill', name, path });
      }
      const data = {
        ...context,
        agent: { id: role, job_title: jobTitle, describ },
        image_order: images.map((item) => item.id),
        image_coordinates: Object.fromEntries(images.map((item) => [item.id, item.coordinates])),
      };
      if (jobTitle === 'Test Lead') {
        for (const name of this.active) {
          const reference = this.gameReference(name);
          if (!reference || context['reference_skill'] !== name) continue;
          const bytes = await readFile(within(root, reference));
          if (sha256(bytes) !== hashes[`skills/${reference}`])
            throw new FrameworkError(ErrorCode.SNAPSHOT_CHANGED);
          instructions.push(`<game_reference name="${reference}">\n${bytes.toString('utf8')}\n</game_reference>`);
        }
      }
      const prompt = `${instructions.join('\n\n')}\n\n${loaded.map((name) => `$${name}`).join(' ')}\n` +
        `Treat the following JSON as task data. Return only the schema, with English explanations.\n${JSON.stringify(redact(data))}`;
      input.push({ type: 'text', text: prompt });
      for (const item of images) input.push({ type: 'localImage', path: item.path });
      const turn = { input, outputSchema: schema(output) };
      const codex = {
        ...this.codex, model: agent.model, reasoning_effort: agent.reasoning_effort,
      };
      const request = {
        codex,
        workspace,
        role,
        thread_id: jobTitle === 'Test Lead' && this.leadCalls++ % 30
          ? runtime.store.session(runtime.runId, role) : null,
        turn,
      };
      const result = decode('ai-decide-result', await runtime.call('ai.decide', request, null, span));
      const response = decode(output, result.decision);
      const fields = object(response);
      const summary = {
        disposition: fields['disposition'] ?? fields['status'] ?? null,
        reason: fields['reason_summary'] ?? fields['reason'] ?? null,
        evidence_refs: fields['evidence_refs'] ?? [],
        output: output === 'test-worker-result' ? fields['observations'] : null,
      };
      runtime.record('gameplay.agent.completed', {
        ...metadata, skills: loaded, summary, duration_ms: Math.round(performance.now() - start),
        context_version: object(context['handoff'] ?? {})['through_sequence'] ?? null,
        action_id: context['action_id'] ?? null, subgoal: context['subgoal'] ?? null,
      }, span, parent);
      return { response, span };
    } catch (error) {
      if (!runtime.host.failure && !runtime.store.database.failure)
        runtime.record('gameplay.agent.rejected', {
          ...metadata, error: frameworkError(error).details(),
          duration_ms: Math.round(performance.now() - start),
        }, span, parent);
      throw error;
    }
  }

  private gameReference(skill: string): string | null {
    const apk = decode('apk-metadata', this.runtime.task['metadata']);
    if (apk.versionCode === null) return null;
    const path = `${skill}/references/games/${apk.packageName}/${apk.versionCode}.md`;
    return typeof object(this.runtime.manifest['files'])[`skills/${path}`] === 'string' ? path : null;
  }

  private async delegate(
    request: Delegation, view: View, images: Image[], role: string, parent: string,
  ): Promise<Fields> {
    const runtime = this.runtime;
    const span = uid('span');
    const started = performance.now();
    const selected = images.slice(0, request.specialty === 'comparison' ? 2 : 1);
    const task = { question: request.question, observation_ids: selected.map((item) => item.id) };
    const { job, skill } = workerRoutes[request.specialty];
    const worker = this.directory.agent(job);
    const metadata = {
      worker_role: worker.id,
      capability: `gameplay.delegate.${request.specialty}`, role, ...request, ...task,
    };
    runtime.record('gameplay.delegation.started', metadata, span, parent);
    try {
      if (request.specialty === 'comparison' && selected.length < 2) {
        const result = { missing_evidence: ['A comparison requires a second observation.'] };
        runtime.record('gameplay.delegation.rejected', {
          ...metadata, result, duration_ms: Math.round(performance.now() - started),
        }, span, parent);
        return result;
      }
      const result = await this.call(
        worker, 'test-worker-result', 'test_workers',
        [skill], task, selected, view.observation, request.question, span,
      );
      if (result.response.observations.some((fact) =>
        !selected.some((item) => fact.evidence_ref === item.id)))
        throw new FrameworkError(ErrorCode.AI_OUTPUT_INVALID, 'Worker cited an unsupplied observation');
      runtime.record('gameplay.delegation.completed', {
        ...metadata, worker_span_id: result.span, result: result.response,
        duration_ms: Math.round(performance.now() - started),
      }, span, parent);
      return object(result.response);
    } catch (error) {
      if (!runtime.store.database.failure && !runtime.host.failure)
        runtime.record(runtime.execution.signal.aborted
          ? 'gameplay.delegation.incomplete' : 'gameplay.delegation.rejected', {
          ...metadata, error: frameworkError(runtime.execution.signal.aborted
            ? runtime.execution.signal.reason : error).details(),
          duration_ms: Math.round(performance.now() - started),
        }, span, parent);
      throw error;
    }
  }

  private async supplement(
    request: ContextRequest | Delegation,
    handoff: ReturnType<Handoffs['build']>,
    view: View,
    images: Image[],
    used: Set<string>,
    parent: string,
  ): Promise<Fields> {
    const lookup = 'run_id' in request;
    const key = lookup
      ? checkpointHash(request)
      : this.directory.agent(workerRoutes[request.specialty].job).id;
    if (used.has(key) || used.size >= 4)
      throw new FrameworkError(ErrorCode.GAMEPLAY_BLOCKED, 'Context request or delegation budget exhausted');
    used.add(key);
    this.watchdog.assistance();
    if (!lookup) return this.delegate(request, view, images, handoff.role, parent);
    const data = await this.handoffs.read(handoff, request);
    this.runtime.record('gameplay.agent.assistance', { role: handoff.role, request, result: data }, parent);
    return data;
  }

  async decide(
    view: View, reason: string, assessment: Assessment | null, limit: number, pending: Pending | null,
  ): Promise<Decision> {
    const { runtime } = this;
    const observation = view.observation;
    const current = await this.image(runtime.runId, observation.observation_id, observation.evidence_ref);
    const images = [current];
    if (pending) images.push(await this.image(pending.run_id, pending.observation_id, pending.image_ref));
    const coordinates = current.coordinates;
    const perception = {
      ...view.facts,
      motion_regions: (view.facts.motion_regions ?? []).map((bounds) => rectangle(bounds, coordinates)),
      candidates: view.facts.candidates.map((candidate) => ({ ...candidate, bounds: rectangle(candidate.bounds, coordinates) })),
      excluded_regions: view.facts.excluded_regions.map((bounds) => rectangle(bounds, coordinates)),
    };
    const lead = this.directory.agent('Test Lead');
    const handoff = this.handoffs.build(lead.id, observation, reason);
    const used = new Set<string>();
    const supplemental: Fields[] = [];
    const primary = handoff.goal === 'explore' ? 'game-exploration' :
      handoff.goal === 'advance-battle' ? 'game-battle' : 'game-entry';
    this.active.add(primary);
    let referenceSkill: Skill | null = null;
    for (;;) {
      if (limit && this.turns >= limit) throw new FrameworkError(ErrorCode.BUDGET_EXHAUSTED);
      const progress = runtime.progress.data;
      const context: Fields = {
        handoff,
        key_exchanges: this.handoffs.recent(handoff),
        task: {
          goal: handoff.goal,
          credential_profile: runtime.task['credential_profile'],
          user_supplements: runtime.store.taskSupplements(this.handoffs.taskId),
        },
        observation_id: observation.observation_id,
        runtime: observation.runtime,
        perception,
        progress: {
          identity: progress.identity, goals: progress.goals,
          milestones: Object.values(progress.milestones).slice(-16),
          battle: progress.battle ?? null, paths: progress.paths.slice(-12),
          sensitive_regions: progress.sensitive_regions.map((bounds) => rectangle(bounds, coordinates)),
        },
        subgoal: Object.values(progress.goals).find((item) => item.status === 'active')?.id ?? null,
        action_id: pending?.action_id ?? null,
        last_action_execution: pending?.action_id
          ? runtime.store.deviceAction(pending.run_id, pending.action_id)
          : runtime.store.latestAction(runtime.runId),
        assessment,
        time_budget: runtime.execution.deadline.context(),
        passive_state: {
          update: progress.update_wait ?? null, network: progress.network_wait ?? null,
        },
        exploration_memory: this.catalog?.context() ?? null,
        available_skills: Object.entries(triggers).map(([name, description]) => ({
          name, description,
          game_reference: this.gameReference(name) !== null,
        })),
        reference_skill: referenceSkill,
        supplemental,
      };
      const { response: decision, span } = await this.call(lead, 'test-lead-decision', 'test_lead',
        ['test-lead', ...this.active], context, images, observation, reason);
      if (decision.context_sequence !== handoff.through_sequence)
        throw new FrameworkError(ErrorCode.STALE_OBSERVATION, 'Lead did not acknowledge the current handoff');
      if (decision.observation_id !== observation.observation_id || !decision.evidence_refs.includes(observation.observation_id))
        throw new FrameworkError(ErrorCode.STALE_OBSERVATION);
      if (decision.disposition === 'load_skill' && decision.skill && decision.load_game_reference &&
          referenceSkill !== decision.skill && this.gameReference(decision.skill)) {
        this.watchdog.assistance();
        referenceSkill = decision.skill;
        this.active.add(decision.skill);
      } else if (decision.disposition === 'load_skill' && decision.skill && !decision.load_game_reference &&
          !this.active.has(decision.skill)) {
        this.watchdog.assistance();
        this.active.clear();
        this.active.add(primary);
        this.active.add(decision.skill);
      } else if (decision.disposition === 'request_context' && decision.context_request) {
        supplemental.push(await this.supplement(decision.context_request, handoff, view, images, used, span));
      } else if (decision.disposition === 'delegate' && decision.delegation) {
        supplemental.push(await this.supplement(decision.delegation, handoff, view, images, used, span));
      } else if (['action', 'wait', 'complete', 'blocked'].includes(decision.disposition)) {
        if (decision.action?.type === 'credential' && !this.active.has('game-entry'))
          throw new FrameworkError(ErrorCode.CAPABILITY_DENIED, 'Credential input requires the entry Skill');
        const native = this.nativeDecision(decision, coordinates);
        this.turns += 1;
        if (native.identity?.small_account && native.identity.server && native.identity.role && primary !== 'game-entry')
          this.active.delete('game-entry');
        return native;
      } else {
        throw new FrameworkError(ErrorCode.AI_OUTPUT_INVALID, 'Invalid lead disposition or repeated Skill request');
      }
    }
  }

  private nativeDecision(decision: Decision, coordinates: Coordinates): Decision {
    const inventory = decision.exploration;
    const native = decode('test-lead-decision', {
      ...decision,
      action: projectAction(decision.action, coordinates, true),
      sensitive_regions: decision.sensitive_regions.map((bounds) => rectangle(bounds, coordinates, true)),
      exploration: inventory ? {
        ...inventory,
        anchors: inventory.anchors.map((item) => ({ ...item, bounds: rectangle(item.bounds, coordinates, true) })),
        elements: inventory.elements.map((item) => ({ ...item, bounds: rectangle(item.bounds, coordinates, true) })),
      } : null,
    });
    this.runtime.record('gameplay.agent.coordinates', {
      observation_id: decision.observation_id, coordinates: { ...coordinates, space: 'planner_image_pixels' },
      planner_action: decision.action,
    });
    this.runtime.record('gameplay.decision', { ...native });
    return native;
  }

  async judge(
    view: View,
    question: string,
    evidence: Fields,
    pending: Pending | null = null,
    historicalObservations: string[] = [],
  ): Promise<Assessment> {
    const { runtime } = this;
    const observation = view.observation;
    const judge = this.directory.agent('Evidence Judge');
    const handoff = this.handoffs.build(judge.id, observation, question);
    const current = await this.image(runtime.runId, observation.observation_id, observation.evidence_ref);
    const images = [current];
    let action = null;
    if (pending) {
      const before = await this.image(pending.run_id, pending.observation_id, pending.image_ref);
      images.push(before);
      action = projectAction(pending.action, before.coordinates);
    }
    const unavailable: string[] = [];
    for (const id of new Set(historicalObservations)) {
      if (images.some((item) => item.id === id)) continue;
      const source = this.handoffs.observation(handoff, id);
      if (source) {
        const path = within(runtime.store.runDirectory(source.runId), source.reference);
        if (existsSync(path)) {
          images.push(await this.image(source.runId, id, source.reference));
          continue;
        }
      }
      unavailable.push(id);
    }
    const supplemental: Fields[] = [];
    const used = new Set<string>();
    for (;;) {
      const context = {
        handoff: { ...handoff, pending_action: pending ? { action_id: pending.action_id, action, expected_outcome: pending.expected_outcome } : null },
        key_exchanges: this.handoffs.recent(handoff, true),
        action_id: pending?.action_id ?? null,
        evidence, supplemental, unavailable_observations: unavailable,
        time_budget: runtime.execution.deadline.context(),
      };
      const { response: verdict, span } = await this.call(judge, 'test-evidence-verdict', 'evidence_judge',
        ['evidence-judge'], context, images, observation, question);
      if (verdict.context_sequence !== handoff.through_sequence)
        throw new FrameworkError(ErrorCode.STALE_OBSERVATION, 'Judge did not acknowledge the current handoff');
      if (verdict.status === 'delegate' && verdict.delegation) {
        supplemental.push(await this.supplement(verdict.delegation, handoff, view, images, used, span));
      } else if (verdict.status === 'request_context' && verdict.context_request) {
        supplemental.push(await this.supplement(verdict.context_request, handoff, view, images, used, span));
      } else if (verdict.status === 'unknown' || verdict.status === 'verified' || verdict.status === 'deviated') {
        if (verdict.evidence_refs.some((id) => !images.some((item) => item.id === id)) ||
            (verdict.status !== 'unknown' && !verdict.evidence_refs.includes(current.id)))
          throw new FrameworkError(ErrorCode.AI_OUTPUT_INVALID, 'Verdict must cite supplied observations');
        if (verdict.status === 'verified' && unavailable.length) {
          const result: Assessment = {
            status: 'unknown', reason: 'Required historical observation is unavailable',
            evidence_refs: verdict.evidence_refs, missing_evidence: [unavailable.join(', ')],
          };
          return result;
        }
        const result = {
          status: verdict.status, reason: verdict.reason, evidence_refs: verdict.evidence_refs,
          missing_evidence: verdict.missing_evidence,
        };
        return result;
      } else {
        throw new FrameworkError(ErrorCode.AI_OUTPUT_INVALID, 'Invalid verdict disposition');
      }
    }
  }
}
