import { readFile } from 'node:fs/promises';
import { decode, text } from '@game-automation/contracts';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import { within } from '../storage/values.js';
import type { Runtime } from '../gameplay/runtime.js';
import type { View } from '../gameplay/local.js';
import type { Action, Decision, Observation, Pending } from '../gameplay/types.js';
import { Catalog, signature, type Element, type Locator, type Page } from './catalog.js';
import { canComplete, destination } from './frontier.js';
import { locate } from './locating.js';
import { reviewScroll, scrollViewport } from './scrolling.js';
import { ExplorationSupervisor } from './supervision.js';

function pendingAction(runtime: Runtime): Pending | null {
  return runtime.progress.data.pending;
}

export class Exploration {
  readonly catalog: Catalog;
  readonly supervisor: ExplorationSupervisor;
  private previousNovelty = 0;
  private scrollKey: string | null = null;
  constructor(readonly runtime: Runtime) {
    this.catalog = new Catalog(runtime);
    this.supervisor = new ExplorationSupervisor(this.catalog);
    this.previousNovelty = this.catalog.session()?.data.novelty ?? 0;
  }

  page(): Page | null {
    const id = this.catalog.session()?.data.page_id;
    return (
      this.catalog
        .records()
        .find((record): record is Page => record.kind === 'page' && record.id === id) ?? null
    );
  }

  hint(): object | null {
    const next = destination(this.catalog);
    if (!next) return null;
    const result = {
      element_id: next.element.id,
      label: next.element.data.label,
      page_id: next.element.parent_id,
      expected_page: next.expectedPage,
      navigating: next.navigating,
    };
    return result;
  }

  async attempt(view: View): Promise<Observation | null> {
    if (this.runtime.progress.data.pending || !view.facts.stable) return null;
    const session = this.catalog.session();
    if (session?.data.external) return this.closeExternal(view.observation);
    const next = destination(this.catalog);
    const page = this.page();
    if (!next || !page || !page.data.scan_complete || next.element.data.intent === 'close')
      return null;
    const element = next.element;
    if (element.data.risk !== 'none' || element.data.intent === 'resource') return null;
    const locator = this.catalog
      .records()
      .find(
        (record): record is Locator =>
          record.kind === 'locator' && record.id === element.data.locator_id && record.data.active,
      );
    if (!locator) return null;
    const display = view.observation.runtime.focused_display;
    if (
      !display ||
      locator.data.layout !==
        signature([
          page.data.layout,
          display.display_id,
          display.rotation_degrees,
          display.width,
          display.height,
        ])
    )
      return null;
    const found = await locate(this.runtime, element, locator, page, view.observation);
    if (!found?.bounds) return null;
    const [left, top, right, bottom] = found.bounds;
    const scroll = element.data.kind === 'scroll';
    const action = decode('gameplay-action', {
      type: scroll ? 'swipe' : 'tap',
      x: Math.floor((left + right) / 2),
      y: Math.floor(scroll ? bottom - (bottom - top) * 0.15 : (top + bottom) / 2),
      end_x: scroll ? Math.floor((left + right) / 2) : 0,
      end_y: scroll ? Math.floor(top + (bottom - top) * 0.15) : 0,
      duration_ms: scroll ? 450 : 1,
      bounds: found.bounds,
      credential_field: null,
    });
    this.runtime.begin('exploration-local');
    const viewport = scroll
      ? await scrollViewport(this.runtime, view.observation, found.bounds)
      : '';
    const mode = scroll ? 'scroll' : next.navigating ? 'navigate' : 'target';
    const link = {
      workspace_key: this.catalog.key,
      element_id: element.id,
      from_page: page.id,
      locator_id: locator.id,
      viewport,
      mode,
      expected_page: next.expectedPage,
      scroll_bounds: scroll ? found.bounds : null,
    };
    const validated = decode('gameplay-progress', {
      ...this.runtime.progress.data,
      pending: {
        observation_id: view.observation.observation_id,
        action,
        run_id: this.runtime.runId,
        image_ref: view.observation.evidence_ref,
        expected_outcome: '',
        exploration: link,
      },
    }).pending;
    const id = await this.runtime.perform(
      action,
      view.observation,
      'exploration-local',
      found.target_guard,
      found.page_guard,
      validated?.exploration ?? null,
    );
    const pending = pendingAction(this.runtime);
    if (!pending) throw new FrameworkError(ErrorCode.INVALID_STATE);
    element.data.bounds = found.bounds;
    this.catalog.save([element]);
    this.bind(
      pending,
      id,
      element,
      page.id,
      scroll ? 'scroll' : next.navigating ? 'navigate' : 'target',
    );
    if (pending.exploration) {
      pending.exploration.expected_page = next.expectedPage;
      pending.exploration.viewport = viewport;
      pending.exploration.scroll_bounds = scroll ? found.bounds : null;
    }
    pending.expected_outcome = scroll
      ? 'Expose the next content viewport'
      : `Open ${element.data.label}`;
    this.runtime.progress.save();
    return view.observation;
  }

  bind(
    pending: Pending,
    actionId: string,
    element: Element,
    fromPage: string,
    mode: NonNullable<Pending['exploration']>['mode'],
  ): void {
    pending.exploration ??= {
      workspace_key: this.catalog.key,
      element_id: element.id,
      from_page: fromPage,
      locator_id: element.data.locator_id,
      viewport: '',
      mode,
    };
    element.status = 'in_progress';
    element.data.action_id = actionId;
    this.catalog.save([element]);
  }

  async review(decision: Decision, view: View, pending: Pending | null): Promise<Page | null> {
    this.scrollKey = null;
    this.catalog.reconcileLedger();
    const inventory = decision.exploration;
    if (!inventory)
      throw new FrameworkError(ErrorCode.AI_OUTPUT_INVALID, 'Exploration inventory missing');
    const page = this.catalog.ingest(inventory, view.observation, view.facts);
    if (pending) {
      if (inventory.kind === 'external') await this.noteExternal(pending, view.observation);
      const link = pending.exploration;
      if (link?.mode === 'scroll' && link.scroll_bounds && link.viewport)
        this.scrollKey = await scrollViewport(this.runtime, view.observation, link.scroll_bounds);
    }
    this.trackProgress();
    return page;
  }

  outcome(decision: Decision, view: View, pending: Pending, page: Page | null): void {
    const previouslyVerified = this.wasVerified(pending);
    const verified = decision.previous_outcome.status === 'verified';
    this.catalog.outcome(pending, page, view.observation, verified);
    if (verified && decision.exploration && this.scrollKey)
      reviewScroll(
        this.runtime,
        this.catalog,
        decision.exploration,
        view.observation,
        pending,
        this.scrollKey,
      );
    if (pending.exploration?.mode === 'external_close') this.finishExternal(view.observation);
    this.trackProgress();
    this.supervisor.review(
      pending,
      page,
      view.observation,
      !previouslyVerified && this.wasVerified(pending),
    );
  }

  private wasVerified(pending: Pending): boolean {
    return this.catalog
      .records()
      .some(
        (record) =>
          record.kind === 'element' &&
          record.id === pending.exploration?.element_id &&
          record.data.ever_verified === true,
      );
  }

  private trackProgress(): void {
    const novelty = this.catalog.session()?.data.novelty ?? 0;
    if (novelty > this.previousNovelty) {
      this.previousNovelty = novelty;
      this.runtime.record('exploration.progress', { novelty });
    }
  }

  async verifyKnown(view: View): Promise<boolean> {
    const pending = this.runtime.progress.data.pending;
    const expected = pending?.exploration?.expected_page;
    if (!pending?.action_id || !expected || !view.facts.stable) return false;
    const records = this.catalog.records();
    const page = records.find(
      (record): record is Page => record.kind === 'page' && record.id === expected,
    );
    if (!page) return false;
    const element = records.find(
      (record): record is Element =>
        record.kind === 'element' &&
        record.parent_id === expected &&
        record.data.locator_id !== null,
    );
    const locator = records.find(
      (record): record is Locator =>
        record.kind === 'locator' &&
        record.id === element?.data.locator_id &&
        record.data.active &&
        record.data.verified,
    );
    if (
      !element ||
      !locator ||
      !(await locate(this.runtime, element, locator, page, view.observation))
    )
      return false;
    const outcome = {
      source: 'verified-page-anchors',
      observation_run: this.runtime.runId,
      observation_id: view.observation.observation_id,
      page_id: page.id,
      business_goal_verified: false,
    };
    this.runtime.store.database.transaction(() => {
      const previouslyVerified = this.wasVerified(pending);
      this.runtime.outcome(pending.run_id, text(pending.action_id), 'verified', outcome);
      this.catalog.outcome(pending, page, view.observation, true);
      this.catalog.move(page, view.observation);
      this.runtime.progress.data.pending = null;
      this.runtime.checkpoint(view.observation);
      this.supervisor.review(
        pending,
        page,
        view.observation,
        !previouslyVerified && this.wasVerified(pending),
      );
    });
    return true;
  }

  async intent(
    action: Action,
    page: Page | null,
    observation: Observation,
  ): Promise<Pending['exploration'] | null> {
    if (!page || !['tap', 'swipe', 'back'].includes(action.type)) return null;
    const elements = this.catalog.records().filter((record): record is Element => {
      if (
        record.kind !== 'element' ||
        record.parent_id !== page.id ||
        record.data.observation_id !== observation.observation_id
      )
        return false;
      const bounds = record.data.bounds;
      return (
        bounds[0] <= action.x &&
        action.x < bounds[2] &&
        bounds[1] <= action.y &&
        action.y < bounds[3] &&
        (action.type === 'swipe') === (record.data.kind === 'scroll')
      );
    });
    elements.sort(
      (a, b) =>
        (a.data.bounds[2] - a.data.bounds[0]) * (a.data.bounds[3] - a.data.bounds[1]) -
        (b.data.bounds[2] - b.data.bounds[0]) * (b.data.bounds[3] - b.data.bounds[1]),
    );
    const element =
      action.type === 'back' ? this.catalog.backTarget(page, observation) : elements[0];
    if (!element) return null;
    const next = destination(this.catalog);
    const scroll = action.type === 'swipe';
    const link: NonNullable<Pending['exploration']> = {
      workspace_key: this.catalog.key,
      element_id: element.id,
      from_page: page.id,
      locator_id: element.data.locator_id,
      viewport:
        scroll && action.bounds
          ? await scrollViewport(this.runtime, observation, action.bounds)
          : '',
      mode: scroll
        ? 'scroll'
        : next?.navigating && next.element.id === element.id
          ? 'navigate'
          : 'target',
      expected_page: next?.navigating && next.element.id === element.id ? next.expectedPage : null,
      scroll_bounds: scroll ? action.bounds : null,
    };
    return link;
  }

  complete(page: Page | null): boolean {
    return canComplete(this.catalog, page);
  }

  permit(action: Action, page: Page | null): boolean {
    if (!page || !['tap', 'swipe'].includes(action.type)) return true;
    const targets = this.catalog.records().filter((record): record is Element => {
      if (
        record.kind !== 'element' ||
        record.parent_id !== page.id ||
        record.data.observation_id !== page.data.observation_id
      )
        return false;
      const [left, top, right, bottom] = record.data.bounds;
      return (
        left <= action.x &&
        action.x < right &&
        top <= action.y &&
        action.y < bottom &&
        record.data.kind === (action.type === 'swipe' ? 'scroll' : 'tap')
      );
    });
    if (!targets.length) return false;
    const selected = targets.sort(
      (a, b) =>
        (a.data.bounds[2] - a.data.bounds[0]) * (a.data.bounds[3] - a.data.bounds[1]) -
        (b.data.bounds[2] - b.data.bounds[0]) * (b.data.bounds[3] - b.data.bounds[1]),
    )[0];
    if (!selected) return false;
    const next = destination(this.catalog);
    return (
      selected.status === 'pending' ||
      (selected.status === 'verified' &&
        selected.data.intent === 'close' &&
        page.data.kind === 'modal' &&
        selected.data.risk === 'none') ||
      (selected.status === 'verified' &&
        selected.data.risk === 'none' &&
        next?.navigating === true &&
        selected.id === next.element.id)
    );
  }

  async afterDispatch(
    pending: Pending,
    id: string,
    page: Page,
    observation: Observation,
  ): Promise<void> {
    if (pending.action.type === 'back' && pending.exploration) {
      const element = this.catalog
        .records()
        .find(
          (record): record is Element =>
            record.kind === 'element' && record.id === pending.exploration?.element_id,
        );
      if (element) this.bind(pending, id, element, page.id, pending.exploration.mode);
      return;
    }
    this.catalog.associate(pending, id, page);
    if (pending.action.type === 'swipe' && pending.exploration && pending.action.bounds) {
      pending.exploration.viewport = await scrollViewport(
        this.runtime,
        observation,
        pending.action.bounds,
      );
      pending.exploration.scroll_bounds = pending.action.bounds;
    }
  }

  private async noteExternal(pending: Pending, observation: Observation): Promise<void> {
    const link = pending.exploration;
    if (!link || pending.action.type !== 'tap') return;
    const last = this.runtime.store.latestAction(this.runtime.runId);
    if (!last || last['action_id'] !== pending.action_id) return;
    const request = decode(
      'device-execute-request',
      JSON.parse(
        await readFile(
          within(this.runtime.store.runDirectory(text(last['run_id'])), text(last['input_ref'])),
          'utf8',
        ),
      ),
    );
    const before = request.runtime;
    const after = observation.runtime;
    const window = after.foreground_window;
    const previous = before.foreground_window;
    const main = after.main_process;
    const sameProcess =
      before.boot_id === after.boot_id &&
      before.main_process &&
      main &&
      before.main_process.pid === main.pid &&
      before.main_process.start_ticks === main.start_ticks;
    const external =
      window &&
      previous &&
      sameProcess &&
      window.window_id !== previous.window_id &&
      !before.window_ids?.includes(window.window_id) &&
      (window.package_name !== before.package_name || window.task_id !== previous.task_id);
    if (!external || !main || !window) {
      this.runtime.record('exploration.external.unknown', { action_id: pending.action_id });
      throw new FrameworkError(ErrorCode.GAMEPLAY_BLOCKED, 'External window origin is uncertain');
    }
    const session = this.catalog.session();
    if (!session) return;
    session.data.external = {
      element_id: link.element_id,
      page_id: link.from_page,
      window_id: window.window_id,
      process_start: main.start_ticks,
      attempts: 0,
    };
    this.catalog.save([session]);
    this.runtime.record('exploration.external.opened', {
      element_id: link.element_id,
      window,
      origin_observation: pending.observation_id,
    });
  }

  private async closeExternal(observation: Observation): Promise<Observation | null> {
    const session = this.catalog.session();
    const external = session?.data.external;
    if (!session || !external) return null;
    if (
      observation.runtime.foreground_window?.window_id !== external.window_id ||
      observation.runtime.main_process?.start_ticks !== external.process_start
    )
      return null;
    if (external.attempts >= 2)
      throw new FrameworkError(
        ErrorCode.GAMEPLAY_BLOCKED,
        'External window closure remains unverified',
      );
    external.attempts += 1;
    this.catalog.save([session]);
    const action = decode('gameplay-action', {
      type: 'back',
      x: 0,
      y: 0,
      end_x: 0,
      end_y: 0,
      duration_ms: 1,
      bounds: null,
      credential_field: null,
    });
    this.runtime.begin('close-external-window');
    const link: NonNullable<Pending['exploration']> = {
      workspace_key: this.catalog.key,
      element_id: external.element_id,
      from_page: external.page_id,
      locator_id: null,
      viewport: '',
      mode: 'external_close',
    };
    const id = await this.runtime.perform(
      action,
      observation,
      'exploration-external',
      null,
      null,
      link,
    );
    const pending = this.runtime.progress.data.pending;
    const element = this.catalog
      .records()
      .find(
        (record): record is Element =>
          record.kind === 'element' && record.id === external.element_id,
      );
    if (pending && element) {
      this.bind(pending, id, element, external.page_id, 'external_close');
      pending.expected_outcome = 'Close only the newly opened external page and return to the game';
      this.runtime.progress.save();
    }
    return observation;
  }

  private finishExternal(observation: Observation): void {
    const session = this.catalog.session();
    const external = session?.data.external;
    if (!session || !external) return;
    const current = observation.runtime;
    if (
      current.focused_display?.package_name !== this.runtime.task['target'] ||
      current.main_process?.start_ticks !== external.process_start ||
      current.window_ids?.includes(external.window_id)
    )
      throw new FrameworkError(ErrorCode.GAMEPLAY_BLOCKED, 'External window closure not proven');
    const element = this.catalog
      .records()
      .find(
        (record): record is Element =>
          record.kind === 'element' && record.id === external.element_id,
      );
    if (element) element.status = 'external_skipped';
    session.data.external = null;
    this.catalog.save(element ? [session, element] : [session]);
    this.runtime.record('exploration.external.closed', { element_id: external.element_id });
  }
}
