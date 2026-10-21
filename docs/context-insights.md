# Context insights

## Reference and attribution

Design reference: [bowenliang123/dsh-context](https://github.com/bowenliang123/dsh-context), version 0.73.3 at commit `811422252efdf51616af7e20d07a65f37d1a15de` (Apache-2.0), inspected on 2026-10-21. No upstream runtime code, dependencies, or UI bundle is vendored or executed by this project.

Relevant implementations:

- [fold.ts](https://github.com/bowenliang123/dsh-context/blob/811422252efdf51616af7e20d07a65f37d1a15de/src/host/fold.ts): durable-event projection and distinct skill attribution.
- [foldWire.ts](https://github.com/bowenliang123/dsh-context/blob/811422252efdf51616af7e20d07a65f37d1a15de/src/host/foldWire.ts): shared read models for summary/detail views.
- [estimate.ts](https://github.com/bowenliang123/dsh-context/blob/811422252efdf51616af7e20d07a65f37d1a15de/src/shared/estimate.ts): explicit heuristic estimates alongside provider figures.

The adopted ideas are event-to-read-model separation, a per-turn context browser, a separate skill category, linked composition/trend selection, and honest distinction between estimates and reported measurements. The implementation here is independent and adapted to our available Codex events.

## Data flow

Persisted events + frozen artifacts -> deterministic Python projection -> CLI / context.json / self-contained HTML.

The viewer does not change authoritative events, start plugins, or call a model. It can inspect an old run even if live skills or current implementation changed. Missing or tampered frozen skill files remain explicitly unavailable.

## What is visible

- Application instructions recorded in thread creation/resume requests.
- Explicitly submitted skills from the run's frozen copies.
- Submitted task text, output schema, and attachment references.
- Public assistant messages, reasoning summaries when supplied, and tool items.
- Module failures, action records, verification events, and run state changes.

An App Server turn is not necessarily one underlying model request. Full provider system instructions, internal history, tokenization, automatic pruning, and complete compacted model context are not reconstructed. Skill reference paths do not prove their contents were read. Costs and window occupancy remain unavailable.

## Usage and composition

Each item's `estimated_tokens` is a local character-count/4 heuristic, explicitly labeled. It is neither billing nor a tokenizer-accurate count, especially for multilingual text. It is not scaled to match provider totals.

Actual usage remains separate. For a new thread, cumulative counters have a known zero baseline. Subsequent observed turns use the difference between current and preceding cumulative counters. Repeated cumulative snapshots and normalized copies of raw events are not added again. A resumed thread without an observed baseline cannot be assigned a complete run total; the last-request and thread-total figures remain inspectable without being substituted for the turn.

Composition includes visible request content and public response content. Trend height uses visible submitted inputs only. Same-thread diffs compare application-submitted items, not the provider's hidden retained history. A compaction event is recorded without inventing removed content.

## Browser behavior

Select a turn to update composition, usage, and the browser. Filter by category or search content. Expand an item to inspect its recorded text, source sequence, digest, and frozen skill path. Execution events remain separate from AI content. The raw call tree and event evidence remain available below.

The report includes a CSP-hashed local script and no external assets. JSON embedded in HTML escapes markup delimiters; the browser renders record contents with textContent. context.json provides the same read model without UI. No price feed, analytics, or external requests are made.
