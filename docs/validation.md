# Validation record

Date: 2026-10-21, Asia/Shanghai. Windows, Python 3.12.10, Codex CLI 0.162.1.

## Original M0 baseline

The original framework passed 34 pytest cases, Ruff lint/format checks, locked uv synchronization, and validation of four skills. Checks covered contracts, capability dependencies/replacement, submission identity, frozen skills, tampering, code changes, nested calls, conflicting event IDs, redaction, HTML escaping, OS locking, cancellation, deadlines, process failures, retries, persistence failures, stale observations, unknown-action prevention, and crash recovery.

Offline Codex tests use a real child-process fake provider. They verify skill inputs, strict optional-field conversion, absent/interrupted summaries, invalid output, and quota errors. They do not prove game behavior.

## Existing local evidence

| Check | Run | Outcome |
|---|---|---|
| Fixture call chain | run_dba7f3fc2a1b40c0ac38c2d109a7d5e8 | succeeded, no model/device calls |
| Actual App Server handshake | run_11a293c5862f4bb8b25cf2a91811d320 | succeeded, handshake only |
| Actual inference smoke | run_53de72b5ada24d609af000e53ddd5c4f | succeeded, stop decision, no device actions |

The inference run reported gpt-6.1-sol, high effort, readOnly sandbox with networkAccess=false, and 859 persisted events. Usage was 9080 input, 185 output, 9265 total tokens; reasoningOutputTokens=51 is a provider-reported subset. No readable reasoning summary was returned, so availability remains false.

These are historical local run artifacts, ignored by Git. Their frozen skill content preserves the language originally submitted and is not rewritten when source skills change. Clone users must regenerate local evidence.

## Context insights extension

Before the English-language update, the suite passed 42 cases including deterministic read-only projection, per-thread usage deltas, repeated-update suppression, missing cumulative baselines, streaming/final message folding, summary/tool identity, frozen-skill browsing, and missing/tampered snapshots. Browser checks verified filters, search, and frozen skill expansion against the existing inference run. See context-insights.md for the referenced upstream commit and visibility boundaries.

After the English-language migration, **45 pytest cases passed**, Ruff lint/format checks passed (46 Python files), and JavaScript syntax validation passed. New checks enforce English maintained text, UTF-8 without BOM, LF endings, and lossless escaped Unicode evidence. Empty usage counters remain unavailable rather than zero.

Fresh English runs also succeeded: `run_43d487f713434d018398cc73dad44803` (fixture demo) and `run_d23eaafd7601459a83f8152ebef02985` (real Codex inference smoke). These are local artifacts and are not committed. Repository publication is verified separately against both remote branch heads.

## M1 intake extension

M1 adds real child-process intake tests for canonical operation without AI, document call trees, exact skill-body delivery, source/default attribution, invented targets, malformed output, budget limits, clarification/resume, unchanged-input retry suppression, cancellation, deadlines, process crashes, and accepted-task reuse at the environment boundary. Document checks cover encoding, size, empty/null data, Unicode preservation, redaction, immutable submissions, and idempotency. A persistence-failure test verifies supplement transactions roll back; state validation and append now occur under one SQLite write transaction.

The final suite passed **69 tests** on Windows/Python 3.12.10. Ruff lint and formatting passed for 52 Python files; all four Skills passed validation, locked dependency synchronization passed, and the maintained-text language/encoding checks passed after documentation updates.

The first real intake run, `run_335718f86ee04d399f6ac6339a39ea16`, exposed missing skill-body injection with path-only inputs under the client's disabled automatic-instruction configuration. The model returned a placeholder target in a needs_input result; semantic validation rejected it. The original failure remains preserved. Historical M0 path-only smoke checks do not establish that the full skills were actually injected.

Prompt assembly now materializes full frozen instructions in the submitted text while retaining explicit skill input items. The successful real run `run_2833d5aa7b3b4bd096c3c1ee44dfd87c` recorded one clarification, one supplement, and one accepted task across two Codex turns and four plugin calls. Both actual turn requests contained the complete project-base and task-intake bodies. The target's provenance points to the supplement event; default limits are attributed to policy. No device action intents occurred. Reported run usage: 20,286 input, 573 output, 20,859 total tokens, including 9,472 cached-input tokens and 183 reasoning-output tokens. Readable summaries remain unavailable. These figures are from the run records, not estimates.

Local evidence is under that run's reports/trace.html and reports/intake-verification.json. The offline projection displays materialized skills once, verifies snapshot digests, and retains mismatched text rather than hiding it. Canonical JSON remains unchanged, including explicitly submitted time limits. This validates task interpretation and recovery using a synthetic identifier; it is not device or real-game acceptance.

## Not yet accepted

Real Android connection, installation, launch, screenshots, clicks, visual recognition, account login, game-path exploration, Word/PDF/OCR ingestion, and game coverage reports are not implemented. Natural-language TXT/Markdown intake is available, with semantic extraction quality beyond the documented smoke case still requiring representative evaluation. Game runs now complete intake and block at environment.check.
