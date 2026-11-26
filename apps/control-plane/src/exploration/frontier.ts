import type { Catalog, Edge, Element, Page, Record } from './catalog.js';

export interface Destination {
  element: Element;
  expectedPage: string | null;
  navigating: boolean;
}

export function destination(catalog: Catalog): Destination | null {
  const session = catalog.session();
  const current = session?.data.page_id;
  if (!current) return null;
  const supervisor = catalog.runtime.progress.data.exploration_supervisor;
  const suspended = supervisor?.scope === catalog.key ? supervisor.feedback?.element_id : null;
  const eligible = (element: Element): boolean =>
    element.status === 'pending' && !element.data.supervision?.quarantined && element.id !== suspended;
  const priority = (element: Element): number =>
    element.data.intent === 'close' ? 2 : element.data.kind === 'scroll' ? 1 : 0;
  const local = catalog.elements(current).filter(eligible).sort((a, b) => priority(a) - priority(b));
  const first = local[0];
  if (first && first.data.intent !== 'close') {
    const result = { element: first, expectedPage: null, navigating: false };
    return result;
  }

  const repository = catalog.runtime.store.exploration;
  const pending = {
    statuses: ['pending'], eligible: true,
    ...(suspended ? { excludeId: suspended } : {}),
  };
  const root = session.data.root_page;
  const hasPending = repository.exists(catalog.key, 'element', pending);
  if (!hasPending && (!root || root === current || session.data.sweep_done)) return null;
  const returnToRoot = !hasPending;
  // Walk only reachable neighborhoods. A sibling outside the route is never materialized.
  const queue: { page: string; first: Edge | null }[] = [{ page: current, first: null }];
  const seen = new Set<string>([current]);
  for (let index = 0; index < queue.length; index += 1) {
    const entry = queue[index];
    if (!entry) break;
    const goalReached = entry.first && (returnToRoot
      ? entry.page === root
      : repository.exists(catalog.key, 'element', { ...pending, parent: entry.page }));
    if (goalReached && entry.first) {
      const edge = entry.first;
      const element = catalog.get('element', edge.data.element_id);
      if (element) {
        const result = { element, expectedPage: edge.data.to_page, navigating: true };
        return result;
      }
    }
    for (const edge of catalog.outgoing(entry.page)) {
      if (seen.has(edge.data.to_page)) continue;
      const element = catalog.get('element', edge.data.element_id);
      if (
        !element ||
        element.data.risk !== 'none' ||
        ['unknown', 'limited', 'prohibited', 'locked'].includes(element.status) ||
        element.data.supervision?.quarantined ||
        element.id === suspended
      )
        continue;
      seen.add(edge.data.to_page);
      queue.push({ page: edge.data.to_page, first: entry.first ?? edge });
    }
  }
  if (first) {
    const result = { element: first, expectedPage: null, navigating: false };
    return result;
  }
  return null;
}

export function canComplete(catalog: Catalog, page: Page | null): boolean {
  const session = catalog.session();
  if (!page?.data.scan_complete || !session || session.data.external) return false;
  // Existence checks cover the whole workspace without reading or capping its records.
  const repository = catalog.runtime.store.exploration;
  if (repository.exists(catalog.key, 'element', { statuses: ['pending', 'in_progress'] }))
    return false;
  if (repository.exists(catalog.key, 'page', { unscanned: true })) return false;
  if (session.data.root_page !== page.id || !session.data.sweep_done) {
    if (session.data.root_page === page.id) {
      session.data.sweep_done = true;
      catalog.save([session]);
    }
    return false;
  }
  const terminal: Record = { ...session, status: 'complete' };
  catalog.save([terminal]);
  return true;
}
