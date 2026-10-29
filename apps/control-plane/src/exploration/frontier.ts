import type { Catalog, Edge, Element, Page, Record } from './catalog.js';

export interface Destination {
  element: Element;
  expectedPage: string | null;
  navigating: boolean;
}

export function destination(catalog: Catalog): Destination | null {
  const records = catalog.records();
  const session = catalog.session();
  const current = session?.data.page_id;
  if (!current) return null;
  const elements = records.filter((record): record is Element => record.kind === 'element');
  const supervisor = catalog.runtime.progress.data.exploration_supervisor;
  const suspended = supervisor?.scope === catalog.key ? supervisor.feedback?.element_id : null;
  const pending = elements.filter(
    (element) =>
      element.status === 'pending' &&
      !element.data.supervision?.quarantined &&
      element.id !== suspended,
  );
  const local = pending.filter((element) => element.parent_id === current);
  local.sort((a, b) => {
    const priority = (element: Element): number =>
      element.data.intent === 'close' ? 2 : element.data.kind === 'scroll' ? 1 : 0;
    return priority(a) - priority(b);
  });
  const first = local[0];
  if (first && first.data.intent !== 'close') {
    const result = { element: first, expectedPage: null, navigating: false };
    return result;
  }
  const goals = new Set(pending.map((element) => element.parent_id));
  if (!pending.length && session?.data.root_page && !session.data.sweep_done)
    goals.add(session.data.root_page);
  const edges = records.filter(
    (record): record is Edge => record.kind === 'edge' && record.status === 'verified',
  );
  const queue: { page: string; first: Edge | null }[] = [{ page: current, first: null }];
  const seen = new Set<string>([current]);
  for (let index = 0; index < queue.length; index += 1) {
    const entry = queue[index];
    if (!entry) break;
    if (goals.has(entry.page) && entry.first) {
      const edge = entry.first;
      const element = elements.find((element) => element.id === edge.data.element_id);
      if (element) {
        const result = { element, expectedPage: edge.data.to_page, navigating: true };
        return result;
      }
    }
    for (const edge of edges) {
      if (edge.data.from_page !== entry.page || seen.has(edge.data.to_page)) continue;
      const element = elements.find((element) => element.id === edge.data.element_id);
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
  if (
    catalog
      .records()
      .some(
        (record) => record.kind === 'element' && ['pending', 'in_progress'].includes(record.status),
      )
  )
    return false;
  if (catalog.records().some((record) => record.kind === 'page' && !record.data.scan_complete))
    return false;
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
