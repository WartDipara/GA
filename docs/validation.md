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

## Not yet accepted

Real Android connection, installation, launch, screenshots, clicks, visual recognition, account login, game-path exploration, natural-language document parsing, and game coverage reports are not implemented. A game run blocking on missing task.intake is expected at M0.
