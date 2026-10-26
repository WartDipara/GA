import { decode } from '@game-automation/contracts';
import { within } from '../storage/values.js';
import type { Runtime } from '../gameplay/runtime.js';
import type { Action, Observation, Pending } from '../gameplay/types.js';
import type { Catalog, Element, Inventory, Scroll } from './catalog.js';

export async function scrollViewport(
  runtime: Runtime,
  observation: Observation,
  bounds: NonNullable<Action['bounds']>,
): Promise<string> {
  const request = { image_path: within(runtime.directory, observation.evidence_ref), bounds };
  const result = decode(
    'perception-viewport-result',
    await runtime.call('perception.viewport', request),
  );
  return result.key;
}

export async function reviewScroll(
  runtime: Runtime,
  catalog: Catalog,
  inventory: Inventory,
  observation: Observation,
  pending: Pending,
): Promise<void> {
  const link = pending.exploration;
  if (!link || link.mode !== 'scroll' || !link.scroll_bounds || !link.viewport) return;
  const records = catalog.records();
  const element = records.find(
    (record): record is Element => record.kind === 'element' && record.id === link.element_id,
  );
  if (!element) return;
  const prior = records.find(
    (record): record is Scroll => record.kind === 'scroll' && record.parent_id === element.id,
  );
  if (prior?.status === 'limited') return;
  const current = await scrollViewport(runtime, observation, link.scroll_bounds);
  const visited = prior?.data.viewports ?? [link.viewport];
  const unchanged = current === link.viewport ? (prior?.data.unchanged ?? 0) + 1 : 0;
  const cycles =
    current !== link.viewport && visited.includes(current) ? (prior?.data.cycles ?? 0) + 1 : 0;
  if (!visited.includes(current)) visited.push(current);
  const review = inventory.scroll_review.find(
    (review) => review.key === element.data.semantic_key || review.key === element.data.key,
  );
  const atEnd = unchanged >= 2 && review?.at_end === true && !review.loading;
  const limited = review?.unbounded === true || cycles >= 2 || visited.length >= 100;
  const reason = limited
    ? review?.unbounded
      ? 'unbounded-content'
      : cycles >= 2
        ? 'repeated-viewport-cycle'
        : 'viewport-limit'
    : atEnd
      ? 'verified-bottom'
      : 'continue';
  const scroll: Scroll = {
    kind: 'scroll',
    id: element.id,
    parent_id: element.id,
    status: limited ? 'limited' : atEnd ? 'complete' : 'pending',
    data: {
      element_id: element.id,
      viewports: visited.slice(-100),
      last_viewport: current,
      unchanged,
      cycles,
      at_end: atEnd,
      reason,
    },
  };
  element.status = limited ? 'limited' : atEnd ? 'verified' : 'pending';
  const session = catalog.session();
  if (session && !prior?.data.viewports.includes(current)) session.data.novelty += 1;
  const changes = session ? [scroll, element, session] : [scroll, element];
  if (limited)
    for (const record of records) {
      if (
        record.kind !== 'element' ||
        record.parent_id !== element.parent_id ||
        record.status !== 'pending' ||
        record.id === element.id
      )
        continue;
      const bounds = record.data.bounds;
      const region = element.data.bounds;
      if (
        bounds[0] >= region[0] &&
        bounds[1] >= region[1] &&
        bounds[2] <= region[2] &&
        bounds[3] <= region[3]
      ) {
        record.status = 'limited';
        changes.push(record);
      }
    }
  catalog.save(changes);
  runtime.record('exploration.scroll', {
    element_id: element.id,
    action_id: pending.action_id,
    before_viewport: link.viewport,
    after_viewport: current,
    unchanged,
    cycles,
    viewports: visited.length,
    reason,
    observation_id: observation.observation_id,
  });
}
