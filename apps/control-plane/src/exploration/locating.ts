import { decode, type ContractMap } from '@game-automation/contracts';
import { performance } from 'node:perf_hooks';
import { within } from '../storage/values.js';
import type { Runtime } from '../gameplay/runtime.js';
import type { Observation } from '../gameplay/types.js';
import type { Element, Locator, Page } from './catalog.js';

export async function locate(
  runtime: Runtime,
  element: Element,
  locator: Locator,
  page: Page,
  observation: Observation,
): Promise<ContractMap['perception-locate-result'] | null> {
  const started = performance.now();
  if (
    runtime.store.run(locator.data.source_run)['task_id'] !==
    runtime.store.run(runtime.runId)['task_id']
  )
    return null;
  const display = observation.runtime.focused_display;
  if (
    !display ||
    display.package_name !== runtime.task['target'] ||
    observation.sensitive_regions.length
  )
    return null;
  const request = {
    image_path: within(runtime.directory, observation.evidence_ref),
    reference_path: within(
      runtime.store.runDirectory(locator.data.source_run),
      locator.data.image_ref,
    ),
    bounds: locator.data.bounds,
    anchors: locator.data.anchors,
    label: element.data.label,
    controls: observation.controls,
    previous_image_path: null,
    noise_regions: (page.data.motion_samples ?? 0) >= 3 ? (page.data.noise_regions ?? []) : [],
  };
  const result = decode(
    'perception-locate-result',
    await runtime.call('perception.locate', request),
  );
  runtime.record('exploration.location', {
    element_id: element.id,
    locator_id: locator.id,
    observation_id: observation.observation_id,
    ...result,
    duration_ms: Math.round(performance.now() - started),
  });
  if (result.status !== 'matched' || !result.page_match || !result.bounds) return null;
  if (result.source === 'visual' && !locator.data.verified) return null;
  return result;
}
