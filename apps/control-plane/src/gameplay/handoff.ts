import { readFile } from 'node:fs/promises';
import { decode, object, text, type ContractMap } from '@game-automation/contracts';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import type { EventQuery } from '../storage/event-repository.js';
import { redact } from '../utilities/redaction.js';
import { within } from '../utilities/files.js';
import { jsonExcerpt } from '../utilities/json-excerpt.js';
import { type Fields } from '../utilities/values.js';
import type { Runtime } from './runtime.js';
import type { Decision, Observation } from './types.js';

type Handoff = ContractMap['test-agent-handoff'];
export type ContextRequest = NonNullable<Decision['context_request']>;
const excerptBytes = 1_536;
const pageBytes = 24 * 1024;

export class Handoffs {
  readonly taskId: string;
  private readonly priorRuns: Handoff['transcript'];

  constructor(readonly runtime: Runtime) {
    const { store, runId, task } = runtime;
    this.taskId = text(store.run(runId)['task_id']);
    this.priorRuns = store.taskRuns(this.taskId).flatMap((run) => {
      const id = text(run['id']);
      if (id === runId || run['mode'] !== 'gameplay') return [];
      const input = store.input(id);
      if (
        input['instance_id'] !== task['instance_id'] ||
        input['credential_profile'] !== task['credential_profile'] ||
        object(input['metadata'])['sha256'] !== object(task['metadata'])['sha256']
      ) return [];
      return [{ run_id: id, through_sequence: store.watermark(id) }];
    });
  }

  build(role: Handoff['role'], observation: Observation, question: string): Handoff {
    const { runtime } = this;
    const progress = runtime.progress.data;
    const lastPath = progress.paths.findLast((path) => path.status === 'verified');
    const milestone = Object.values(progress.milestones).findLast((item) => item.status === 'verified');
    const transcript = [...this.priorRuns, {
      run_id: runtime.runId,
      through_sequence: runtime.store.watermark(runtime.runId),
    }];
    const prepared = {
      role,
      run_id: runtime.runId,
      through_sequence: transcript.at(-1)?.through_sequence ?? 0,
      goal: runtime.task['goal'],
      question,
      observation_id: observation.observation_id,
      last_verified_milestone: milestone ?? lastPath ?? null,
      pending_action: progress.pending ?? null,
      transcript,
    };
    return decode('test-agent-handoff', prepared);
  }

  recent(handoff: Handoff, independentReview = false): Fields[] {
    const result: Fields[] = [];
    for (const source of handoff.transcript.slice(-2)) {
      const events = this.runtime.store.events(source.run_id, {
        through: source.through_sequence,
        kinds: ['gameplay.agent.completed', 'gameplay.agent.rejected',
          'gameplay.action.not_dispatched', 'gameplay.decision', 'gameplay.outcome'],
        limit: 4,
      });
      for (const event of events) {
        const leadDecision = event.payload['job_title'] === 'Test Lead' ||
          event.payload['role'] === 'test-lead' || event.kind === 'gameplay.decision';
        if (independentReview && leadDecision) continue;
        const output = object(event.payload['summary'] ?? event.payload);
        result.push({
          run_id: source.run_id, event_id: event.event_id, seq: event.seq,
          role: event.payload['role'] ?? null, observation_id: event.payload['observation_id'] ?? null,
          disposition: output['disposition'] ?? output['status'] ?? null,
          reason: output['reason'] ?? output['reason_summary'] ?? null,
          evidence_refs: output['evidence_refs'] ?? [],
        });
      }
    }
    return result;
  }

  observation(handoff: Handoff, id: string): { runId: string; reference: string } | null {
    for (const source of [...handoff.transcript].reverse()) {
      const event = this.runtime.store.events(source.run_id, {
        through: source.through_sequence, kinds: ['gameplay.observation'], limit: 1,
        reference: { field: 'observation', value: id },
      }).at(-1);
      if (event) {
        const result = { runId: source.run_id, reference: text(event.payload['evidence_ref']) };
        return result;
      }
    }
    return null;
  }

  async read(handoff: Handoff, request: ContextRequest): Promise<Fields> {
    const source = handoff.transcript.find((item) => item.run_id === request.run_id);
    if (!source) throw new FrameworkError(ErrorCode.CAPABILITY_DENIED, 'Transcript is outside this test scope');
    const through = Math.min(source.through_sequence, (request.before_sequence ?? Infinity) - 1);
    const { store } = this.runtime;
    const query: EventQuery = { through, limit: 6 };
    if (request.turn) {
      query.span = request.turn;
      query.kinds = ['call.started', 'call.completed'];
      query.capability = 'ai.decide';
    } else if (request.action || request.observation || request.subgoal) {
      const match = request.action ?? request.observation ?? request.subgoal ?? '';
      const field = request.action ? 'action' : request.observation ? 'observation' : 'subgoal';
      query.reference = { field, value: match };
      query.kinds = [
        'gameplay.agent.completed', 'gameplay.observation', 'gameplay.outcome',
        'gameplay.action.origin', 'action.intent', 'action.result',
      ];
    } else {
      query.kinds = ['call.started', 'call.completed'];
      query.capability = 'ai.decide';
    }
    const rows = store.events(source.run_id, query);
    if (request.turn) {
      rows.push(...store.events(source.run_id, {
        span: request.turn, through, limit: 6, kinds: ['ai.item.completed'],
        reference: { field: 'itemType', value: 'agentMessage' },
      }));
      rows.sort((left, right) => left.seq - right.seq);
    }
    const records: Fields[] = [];
    // A cursor continues one record; an ordinary page starts each record at its first excerpt.
    const selected = request.offset === undefined ? rows : rows.slice(-1);
    for (const event of selected) {
      const record: Fields = { seq: event.seq, event_id: event.event_id, kind: event.kind, turn: event.span_id };
      const ref = event.payload['input_ref'] ?? event.payload['output_ref'];
      if (event.kind === 'ai.item.completed') {
        const content = text(object(event.payload['item'])['text']);
        Object.assign(record, jsonExcerpt(content, request.offset ?? 0, excerptBytes));
      } else if (request.turn && typeof ref === 'string') {
        const path = within(store.runDirectory(source.run_id), ref);
        try {
          const raw: unknown = JSON.parse(await readFile(path, 'utf8'));
          const artifact = object(raw);
          const content = event.kind === 'call.started' ? artifact['turn'] : artifact;
          const serialized = JSON.stringify(redact(content ?? null));
          Object.assign(record, jsonExcerpt(serialized, request.offset ?? 0, excerptBytes));
        } catch (error) {
          if (!(error instanceof Error) || !('code' in error) || error.code !== 'ENOENT') throw error;
          record['unavailable'] = true;
        }
      } else if (!query.capability) {
        const serialized = JSON.stringify(redact(event.payload));
        Object.assign(record, jsonExcerpt(serialized, request.offset ?? 0, excerptBytes));
      }
      records.push(record);
    }
    const page = { run_id: source.run_id, records, next_before_sequence: records[0]?.['seq'] ?? null };
    // Trim oldest entries first so the cursor still leads to the omitted records.
    while (Buffer.byteLength(JSON.stringify(page)) > pageBytes && records.length > 1) {
      records.shift();
      page.next_before_sequence = records[0]?.['seq'] ?? null;
    }
    if (Buffer.byteLength(JSON.stringify(page)) > pageBytes)
      throw new FrameworkError(ErrorCode.INVALID_PROTOCOL, 'Transcript record metadata exceeds its byte budget');
    return page;
  }
}
