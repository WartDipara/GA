import { createHash } from 'node:crypto';
import { decode, text, type ContractMap } from '@game-automation/contracts';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import { object } from '@game-automation/contracts';
import { parsed, uid } from '../storage/values.js';
import type { Runtime } from '../gameplay/runtime.js';
import type { Facts, Observation, Pending } from '../gameplay/types.js';
import { destination } from './frontier.js';

export type Record = ContractMap['exploration-record'];
export type Page = Extract<Record, { kind: 'page' }>;
export type Element = Extract<Record, { kind: 'element' }>;
export type Locator = Extract<Record, { kind: 'locator' }>;
export type Edge = Extract<Record, { kind: 'edge' }>;
export type Scroll = Extract<Record, { kind: 'scroll' }>;
export type Session = Extract<Record, { kind: 'session' }>;
export type Inventory = ContractMap['exploration-view'];

export function signature(value: unknown): string {
  return createHash('sha256').update(JSON.stringify(value)).digest('hex');
}

function name(value: string): string {
  return value.normalize('NFKC').trim().toLowerCase();
}

export class Catalog {
  private readonly semanticKeys = new Map<string, string | null>();
  constructor(readonly runtime: Runtime) {}

  private semanticKey(page: Page): string | null {
    if (page.data.semantic_key) return page.data.semantic_key;
    if (this.semanticKeys.has(page.id)) return this.semanticKeys.get(page.id) ?? null;
    const row = this.runtime.store.database.one(
      "SELECT e.data FROM events e JOIN runs r ON r.id=e.run_id WHERE r.task_id=? AND e.kind='gameplay.decision' AND json_extract(e.data,'$.payload.observation_id')=? ORDER BY e.seq DESC LIMIT 1",
      text(this.runtime.store.run(this.runtime.runId)['task_id']),
      page.data.observation_id,
    );
    const event = row ? decode('trace', parsed(row['data'])) : null;
    const view = event?.payload['exploration'];
    const key = view ? decode('exploration-view', view).page_key : null;
    this.semanticKeys.set(page.id, key);
    return key;
  }

  get key(): string {
    return `${this.runtime.progress.key}:exploration:${signature(this.runtime.progress.data.identity)}`;
  }

  records(): Record[] {
    const records = this.runtime.store.exploration(this.key);
    return records;
  }

  session(): Session | undefined {
    return this.records().find((record): record is Session => record.kind === 'session');
  }

  save(records: Record[]): void {
    if (!records.length) return;
    const request = {
      run_id: this.runtime.runId,
      workspace_key: this.key,
      records,
      span_id: this.runtime.step,
    };
    this.runtime.store.command('exploration_commit', request);
  }

  context(): object {
    const records = this.records();
    const session = records.find((record): record is Session => record.kind === 'session');
    const current = session?.data.page_id;
    const elements = records.filter((record): record is Element => record.kind === 'element');
    const page = records.find(
      (record): record is Page => record.kind === 'page' && record.id === current,
    );
    const next = destination(this);
    const feedbackRow = this.runtime.store.database.one(
      "SELECT data FROM events WHERE kind='exploration.proposal.rejected' AND json_extract(data,'$.payload.workspace_key')=? ORDER BY seq DESC LIMIT 1",
      this.key,
    );
    const feedback = feedbackRow ? decode('trace', parsed(feedbackRow['data'])) : null;
    const related = new Set([
      current,
      session?.data.root_page,
      ...elements
        .filter((element) => element.status === 'pending')
        .slice(0, 30)
        .map((element) => element.parent_id),
    ]);
    const result = {
      current_page: page
        ? {
            id: page.id,
            page_key: this.semanticKey(page),
            label: page.data.label,
            variant: page.data.variant,
            layout: page.data.layout,
            anchors: page.data.anchors.map((anchor) => anchor.key),
          }
        : null,
      known_pages: records
        .filter((record): record is Page => record.kind === 'page' && related.has(record.id))
        .slice(0, 12)
        .map((page) => ({
          id: page.id,
          page_key: this.semanticKey(page),
          label: page.data.label,
          variant: page.data.variant,
        })),
      elements: elements
        .filter((record) => record.parent_id === current)
        .slice(0, 120)
        .map((record) => ({
          id: record.id,
          key: record.data.semantic_key,
          label: record.data.label,
          container: record.data.container,
          item: record.data.item,
          kind: record.data.kind,
          status: record.status,
          availability: record.data.availability,
          risk: record.data.risk,
          condition: record.data.condition,
        })),
      frontier: elements
        .filter((record) => record.status === 'pending')
        .slice(0, 30)
        .map((record) => ({ id: record.id, page_id: record.parent_id, label: record.data.label })),
      root_page: session?.data.root_page ?? null,
      sweep_done: session?.data.sweep_done ?? false,
      authority: 'Memory is historical evidence; locate targets in the current observation',
      last_rejected_proposal: feedback
        ? {
            event_id: feedback.event_id,
            source_observation_id: feedback.payload['observation_id'],
            dispatched: false,
            reason: feedback.payload['reason'],
            required_correction: feedback.payload['required_correction'],
          }
        : null,
      recommendation: next
        ? {
            element_id: next.element.id,
            key: next.element.data.semantic_key,
            label: next.element.data.label,
            kind: next.element.data.kind,
            container: next.element.data.container,
            item: next.element.data.item,
            expected_page: next.expectedPage,
            navigating: next.navigating,
          }
        : null,
    };
    return result;
  }

  ingest(view: Inventory, observation: Observation, facts: Facts): Page | null {
    if (!view.authenticated || view.kind === 'external') return null;
    if (!view.inventory_complete && !view.elements.length) return null;
    const records = this.records();
    const key = signature([name(view.page_key), name(view.variant)]);
    const previous = records.find(
      (record): record is Page => record.kind === 'page' && record.data.key === key,
    );
    const page: Page = {
      kind: 'page',
      id: previous?.id ?? uid('page'),
      parent_id: null,
      status: view.inventory_complete ? 'scanned' : 'pending',
      data: {
        key,
        semantic_key: view.page_key,
        label: view.label,
        variant: view.variant,
        layout: view.layout,
        kind: view.kind,
        authenticated: true,
        root: previous?.data.root === true || view.is_root,
        scan_complete: view.inventory_complete,
        observation_id: observation.observation_id,
        anchors: view.anchors,
        recheck: false,
        motion_samples: (previous?.data.motion_samples ?? 0) + 1,
        noise_regions: [],
      },
    };
    const changes: Record[] = [page];
    let novelty = previous ? 0 : 1;
    const display = observation.runtime.focused_display;
    if (!display) throw new FrameworkError(ErrorCode.STALE_OBSERVATION);
    const seen = new Set<string>();
    const sealed = records
      .filter((record): record is Scroll => record.kind === 'scroll' && record.status === 'limited')
      .map((region) =>
        records.find(
          (record): record is Element =>
            record.kind === 'element' && record.id === region.data.element_id,
        ),
      )
      .filter((element) => element?.parent_id === page.id);
    for (const candidate of view.elements) {
      const [left, top, right, bottom] = candidate.bounds;
      if (!(left < right && top < bottom && right <= display.width && bottom <= display.height))
        throw new FrameworkError(ErrorCode.AI_OUTPUT_INVALID, 'Invalid exploration target bounds');
      const targetKey = signature([
        page.id,
        name(candidate.container),
        name(candidate.item),
        name(candidate.key),
        candidate.kind,
      ]);
      if (seen.has(targetKey))
        throw new FrameworkError(
          ErrorCode.AI_OUTPUT_INVALID,
          'Duplicate exploration item identity',
        );
      seen.add(targetKey);
      const known = records.find(
        (record): record is Element =>
          record.kind === 'element' &&
          record.parent_id === page.id &&
          record.data.key === targetKey,
      );
      let status: Element['status'] = known?.status ?? 'pending';
      const blocked = candidate.availability !== 'enabled';
      const prohibited = ['real_payment', 'account_delete', 'unknown'].includes(candidate.risk);
      const inSealedRegion = sealed.some(
        (element) =>
          element &&
          candidate.bounds[0] >= element.data.bounds[0] &&
          candidate.bounds[1] >= element.data.bounds[1] &&
          candidate.bounds[2] <= element.data.bounds[2] &&
          candidate.bounds[3] <= element.data.bounds[3],
      );
      if (known?.status === 'limited' || known?.status === 'external_skipped')
        status = known.status;
      else if (inSealedRegion && status === 'pending') status = 'limited';
      else if (blocked) status = 'locked';
      else if (prohibited) status = 'prohibited';
      else if (known?.status === 'locked') status = 'pending';
      if (!known || (status === 'pending' && known.status !== 'pending')) novelty += 1;
      const element: Element = {
        kind: 'element',
        id: known?.id ?? uid('element'),
        parent_id: page.id,
        status,
        data: {
          ...candidate,
          key: targetKey,
          page_id: page.id,
          observation_id: observation.observation_id,
          semantic_key: candidate.key,
          locator_id: known?.data.locator_id ?? null,
          attempts: known?.data.attempts ?? 0,
          action_id: known?.data.action_id ?? null,
          child_page: known?.data.child_page ?? null,
          ever_verified: known?.data.ever_verified ?? known?.status === 'verified',
        },
      };
      const prior = records.find(
        (record): record is Locator =>
          record.kind === 'locator' && record.id === element.data.locator_id,
      );
      const layout = signature([
        view.layout,
        display.display_id,
        display.rotation_degrees,
        display.width,
        display.height,
      ]);
      if (!prior?.data.active || prior.data.layout !== layout) {
        const locator: Locator = {
          kind: 'locator',
          id: uid('locator'),
          parent_id: element.id,
          status: 'candidate',
          data: {
            element_id: element.id,
            page_id: page.id,
            layout,
            observation_id: observation.observation_id,
            source_run: this.runtime.runId,
            image_ref: observation.evidence_ref,
            bounds: candidate.bounds,
            anchors: view.anchors,
            active: true,
            verified: false,
            viewport: view.viewport_key ?? '',
          },
        };
        element.data.locator_id = locator.id;
        changes.push(locator);
      }
      changes.push(element);
    }
    const motion = facts.motion_regions ?? [];
    const previousNoise = previous?.data.noise_regions;
    page.data.noise_regions = motion.filter((box) => {
      const recurrent =
        !previousNoise ||
        previousNoise.some((prior) => prior.every((value, index) => value === box[index]));
      const overTarget = view.elements.some((element) => {
        const target = element.bounds;
        return box[0] < target[2] && box[2] > target[0] && box[1] < target[3] && box[3] > target[1];
      });
      return recurrent && !overTarget;
    });
    const priorSession = records.find((record): record is Session => record.kind === 'session');
    const session: Session = {
      kind: 'session',
      id: 'current',
      parent_id: null,
      status: 'active',
      data: {
        ...priorSession?.data,
        page_id: page.id,
        root_page: priorSession?.data.root_page ?? (view.is_root ? page.id : null),
        observation_id: observation.observation_id,
        identity_key: signature(this.runtime.progress.data.identity),
        sweep_done: novelty ? false : (priorSession?.data.sweep_done ?? false),
        novelty: (priorSession?.data.novelty ?? 0) + novelty,
      },
    };
    changes.push(session);
    this.save(changes);
    return page;
  }

  associate(
    pending: Pending,
    actionId: string,
    page: Page,
    mode: NonNullable<Pending['exploration']>['mode'] = 'target',
  ): void {
    const action = pending.action;
    const elements = this.records().filter(
      (record): record is Element =>
        record.kind === 'element' &&
        record.parent_id === page.id &&
        record.data.observation_id === pending.observation_id,
    );
    const candidates = elements
      .filter((element) => {
        const [left, top, right, bottom] = element.data.bounds;
        return (
          left <= action.x &&
          action.x < right &&
          top <= action.y &&
          action.y < bottom &&
          (action.type === 'swipe' ? element.data.kind === 'scroll' : element.data.kind === 'tap')
        );
      })
      .sort((a, b) => {
        const area = (element: Element): number =>
          (element.data.bounds[2] - element.data.bounds[0]) *
          (element.data.bounds[3] - element.data.bounds[1]);
        return area(a) - area(b);
      });
    const element = candidates[0];
    if (!element || !['tap', 'swipe'].includes(action.type)) return;
    element.status = 'in_progress';
    element.data.action_id = actionId;
    pending.exploration ??= {
      workspace_key: this.key,
      element_id: element.id,
      from_page: page.id,
      locator_id: element.data.locator_id,
      viewport: '',
      mode: action.type === 'swipe' ? 'scroll' : mode,
    };
    this.save([element]);
  }

  move(page: Page, observation: Observation): void {
    const session = this.session();
    if (!session) return;
    session.data.page_id = page.id;
    session.data.observation_id = observation.observation_id;
    this.save([session]);
  }

  reconcileLedger(): void {
    const changes: Record[] = [];
    for (const element of this.records()) {
      if (element.kind !== 'element' || element.status !== 'in_progress' || !element.data.action_id)
        continue;
      const row = this.runtime.store.database.one(
        'SELECT a.status,a.result FROM actions a JOIN runs r ON r.id=a.run_id WHERE r.task_id=? AND a.action_id=?',
        text(this.runtime.store.run(this.runtime.runId)['task_id']),
        element.data.action_id,
      );
      if (!row || !['verified', 'reconciled'].includes(text(row['status']))) continue;
      const evidence = object(parsed(row['result'])['result']);
      if (!evidence['observation_id']) continue;
      element.status =
        element.data.kind === 'scroll'
          ? 'pending'
          : row['status'] === 'verified'
            ? 'verified'
            : 'unknown';
      if (element.status === 'verified') element.data.ever_verified = true;
      changes.push(element);
      this.runtime.record('exploration.association.restored', {
        element_id: element.id,
        action_id: element.data.action_id,
        ledger_status: row['status'],
        evidence,
      });
    }
    this.save(changes);
  }

  backTarget(page: Page, observation: Observation): Element {
    const known = this.records().find(
      (record): record is Element =>
        record.kind === 'element' && record.parent_id === page.id && record.data.kind === 'back',
    );
    if (known) return known;
    const display = observation.runtime.focused_display;
    if (!display) throw new FrameworkError(ErrorCode.STALE_OBSERVATION);
    const element: Element = {
      kind: 'element',
      id: uid('element'),
      parent_id: page.id,
      status: 'pending',
      data: {
        key: signature([page.id, 'system-back']),
        semantic_key: 'system-back',
        label: 'Android Back',
        container: 'system-navigation',
        item: '',
        kind: 'back',
        intent: 'close',
        bounds: [0, 0, display.width, display.height],
        risk: 'none',
        availability: 'enabled',
        condition: '',
        page_id: page.id,
        observation_id: observation.observation_id,
        locator_id: null,
        attempts: 0,
        action_id: null,
        child_page: null,
        ever_verified: false,
      },
    };
    this.save([element]);
    return element;
  }

  outcome(pending: Pending, page: Page | null, observation: Observation, verified: boolean): void {
    const link = pending.exploration;
    if (!link || link.workspace_key !== this.key || !pending.action_id) return;
    const records = this.records();
    const assigned = records.filter(
      (record): record is Element =>
        record.kind === 'element' && record.data.action_id === pending.action_id,
    );
    if (assigned.length > 1) throw new FrameworkError(ErrorCode.INVALID_STATE);
    const element =
      assigned[0] ??
      records.find(
        (record): record is Element => record.kind === 'element' && record.id === link.element_id,
      );
    if (!element) throw new FrameworkError(ErrorCode.INVALID_STATE);
    if (element.id !== link.element_id) {
      this.runtime.record('exploration.association.reconciled', {
        action_id: pending.action_id,
        previous_element_id: link.element_id,
        element_id: element.id,
      });
      link.element_id = element.id;
    }
    const locator = records.find(
      (record): record is Locator => record.kind === 'locator' && record.id === link.locator_id,
    );
    element.data.child_page = page?.id ?? null;
    const changes: Record[] = [element];
    const firstVerification = verified && !element.data.ever_verified && link.mode !== 'scroll';
    if (verified && link.mode !== 'scroll') element.data.ever_verified = true;
    if (!verified) element.data.attempts += 1;
    if (link.mode === 'scroll') element.status = 'pending';
    else if (link.mode === 'external_close') element.status = 'in_progress';
    else
      element.status = verified ? 'verified' : element.data.attempts >= 2 ? 'unknown' : 'pending';
    if (locator && link.mode !== 'external_close' && link.mode !== 'return') {
      locator.status = verified ? 'verified' : 'stale';
      locator.data.verified = verified;
      locator.data.active = verified;
      changes.push(locator);
    }
    if (
      verified &&
      page &&
      pending.action.type !== 'swipe' &&
      ['tap', 'back', 'return'].includes(pending.action.type)
    ) {
      const edge = decode('exploration-record', {
        kind: 'edge',
        id: signature([link.from_page, element.id, page.id]),
        parent_id: link.from_page,
        status: 'verified',
        data: {
          from_page: link.from_page,
          to_page: page.id,
          element_id: element.id,
          action_id: pending.action_id,
          observation_id: observation.observation_id,
          action_type: pending.action.type,
        },
      });
      changes.push(edge);
    }
    const session = this.session();
    if (firstVerification && session) {
      session.data.novelty += 1;
      session.data.sweep_done = false;
      changes.push(session);
    }
    this.save(changes);
  }
}
