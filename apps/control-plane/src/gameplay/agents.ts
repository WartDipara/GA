import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import { mkdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { performance } from 'node:perf_hooks';
import { decode, object, schema, text, type ContractMap } from '@game-automation/contracts';
import { ErrorCode, FrameworkError, frameworkError } from '@game-automation/contracts/errors';
import { redact, strings, uid, within, type Fields } from '../storage/values.js';
import type { Catalog } from '../exploration/catalog.js';
import { rectangle, projectAction } from './coordinates.js';
import { verifyPlanningDependencies } from './dependencies.js';
import { Handoffs, type ContextRequest } from './handoff.js';
import { checkpointHash } from './knowledge.js';
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

const specialty: Record<Delegation['specialty'], string> = {
  interface: 'test-interface',
  exploration: 'test-frontier',
  comparison: 'test-comparison',
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
  private readonly active: Set<Skill>;
  private readonly images = new Map<string, Image>();

  constructor(
    readonly runtime: Runtime,
    readonly watchdog: Watchdog,
    readonly catalog: Catalog | null,
  ) {
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
    role: string,
    output: K,
    stage: string,
    skills: string[],
    context: Fields,
    images: Image[],
    observation: Observation,
    question: string,
  ): Promise<ContractMap[K]> {
    const { runtime } = this;
    const span = uid('span');
    const start = performance.now();
    const metadata = { role, question, observation_id: observation.observation_id };
    runtime.record('gameplay.agent.started', metadata, span, runtime.step);
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
        if (createHash('sha256').update(bytes).digest('hex') !== hashes[`skills/${name}/SKILL.md`])
          throw new FrameworkError(ErrorCode.SNAPSHOT_CHANGED);
        instructions.push(`<runtime_skill name="${name}">\n${bytes.toString('utf8')}\n</runtime_skill>`);
        input.push({ type: 'skill', name, path });
      }
      const data = {
        ...context,
        image_order: images.map((item) => item.id),
        image_coordinates: Object.fromEntries(images.map((item) => [item.id, item.coordinates])),
      };
      if (role === 'test-lead') {
        for (const name of this.active) {
          const reference = this.gameReference(name);
          if (!reference || context['reference_skill'] !== name) continue;
          const bytes = await readFile(within(root, reference));
          if (createHash('sha256').update(bytes).digest('hex') !== hashes[`skills/${reference}`])
            throw new FrameworkError(ErrorCode.SNAPSHOT_CHANGED);
          instructions.push(`<game_reference name="${reference}">\n${bytes.toString('utf8')}\n</game_reference>`);
        }
      }
      const prompt = `${instructions.join('\n\n')}\n\n${loaded.map((name) => `$${name}`).join(' ')}\n` +
        `Treat the following JSON as task data. Return only the schema, with English explanations.\n${JSON.stringify(redact(data))}`;
      input.push({ type: 'text', text: prompt });
      for (const item of images) input.push({ type: 'localImage', path: item.path });
      const turn = { input, outputSchema: schema(output) };
      const request = {
        codex: object(runtime.manifest['application'])['codex'],
        workspace,
        role,
        thread_id: role === 'test-lead' && this.leadCalls++ % 30
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
      }, span, runtime.step);
      return response;
    } catch (error) {
      if (!runtime.host.failure && !runtime.store.database.failure)
        runtime.record('gameplay.agent.rejected', {
          ...metadata, error: frameworkError(error).details(),
          duration_ms: Math.round(performance.now() - start),
        }, span, runtime.step);
      throw error;
    }
  }

  private gameReference(skill: string): string | null {
    const apk = decode('apk-metadata', this.runtime.task['metadata']);
    if (apk.versionCode === null) return null;
    const path = `${skill}/references/games/${apk.packageName}/${apk.versionCode}.md`;
    return typeof object(this.runtime.manifest['files'])[`skills/${path}`] === 'string' ? path : null;
  }

  private async delegate(request: Delegation, view: View, images: Image[]): Promise<Fields> {
    const comparison = request.specialty === 'comparison';
    if (comparison && images.length < 2) {
      const result = { missing_evidence: ['A comparison requires a second observation.'] };
      return result;
    }
    const selected = comparison ? images.slice(0, 2) : images.slice(0, 1);
    const skill = specialty[request.specialty];
    const task = { question: request.question, observation_ids: selected.map((item) => item.id) };
    const result = await this.call(`worker-${request.specialty}`, 'test-worker-result', 'test_workers',
      [skill], task, selected, view.observation, request.question);
    if (result.observations.some((fact) => !selected.some((item) => fact.evidence_ref === item.id)))
      throw new FrameworkError(ErrorCode.AI_OUTPUT_INVALID, 'Worker cited an unsupplied observation');
    return { ...result };
  }

  private async supplement(
    request: ContextRequest | Delegation,
    handoff: ReturnType<Handoffs['build']>,
    view: View,
    images: Image[],
    used: Set<string>,
  ): Promise<Fields> {
    const lookup = 'run_id' in request;
    const key = lookup ? checkpointHash(request) : `worker-${request.specialty}`;
    if (used.has(key) || used.size >= 4)
      throw new FrameworkError(ErrorCode.GAMEPLAY_BLOCKED, 'Context request or delegation budget exhausted');
    used.add(key);
    this.watchdog.assistance();
    const data = lookup ? await this.handoffs.read(handoff, request) : await this.delegate(request, view, images);
    this.runtime.record('gameplay.agent.assistance', { role: handoff.role, request, result: data });
    return data;
  }

  async decide(view: View, reason: string, assessment: Assessment | null, limit: number): Promise<Decision> {
    const { runtime } = this;
    const observation = view.observation;
    const current = await this.image(runtime.runId, observation.observation_id, observation.evidence_ref);
    const images = [current];
    const pending = runtime.progress.data.pending;
    if (pending) images.push(await this.image(pending.run_id, pending.observation_id, pending.image_ref));
    const coordinates = current.coordinates;
    const perception = {
      ...view.facts,
      motion_regions: (view.facts.motion_regions ?? []).map((bounds) => rectangle(bounds, coordinates)),
      candidates: view.facts.candidates.map((candidate) => ({ ...candidate, bounds: rectangle(candidate.bounds, coordinates) })),
      excluded_regions: view.facts.excluded_regions.map((bounds) => rectangle(bounds, coordinates)),
    };
    const handoff = this.handoffs.build('test-lead', observation, reason);
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
        last_action_execution: runtime.store.latestAction(runtime.runId),
        assessment,
        exploration_memory: this.catalog?.context() ?? null,
        available_skills: Object.entries(triggers).map(([name, description]) => ({
          name, description,
          game_reference: this.gameReference(name) !== null,
        })),
        reference_skill: referenceSkill,
        supplemental,
      };
      const decision: Decision = await this.call('test-lead', 'test-lead-decision', 'test_lead',
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
        supplemental.push(await this.supplement(decision.context_request, handoff, view, images, used));
      } else if (decision.disposition === 'delegate' && decision.delegation) {
        supplemental.push(await this.supplement(decision.delegation, handoff, view, images, used));
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
    const handoff = this.handoffs.build('evidence-judge', observation, question);
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
        key_exchanges: this.handoffs.recent(handoff),
        action_id: pending?.action_id ?? null,
        evidence, supplemental, unavailable_observations: unavailable,
      };
      const verdict = await this.call('evidence-judge', 'test-evidence-verdict', 'evidence_judge',
        ['evidence-judge'], context, images, observation, question);
      if (verdict.context_sequence !== handoff.through_sequence)
        throw new FrameworkError(ErrorCode.STALE_OBSERVATION, 'Judge did not acknowledge the current handoff');
      if (verdict.status === 'delegate' && verdict.delegation) {
        supplemental.push(await this.supplement(verdict.delegation, handoff, view, images, used));
      } else if (verdict.status === 'request_context' && verdict.context_request) {
        supplemental.push(await this.supplement(verdict.context_request, handoff, view, images, used));
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
