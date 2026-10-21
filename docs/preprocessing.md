# APK preprocessing

This increment accepts a JSON list, downloads each APK, and prepares compact metadata for later
modules. It does not install, modify, launch, or explore applications. Verification of this code
change is compilation only; live download and APK-tool behavior remain unverified.

## Input and commands

Put a private UTF-8 JSON file under local `var/`:

```json
{
  "apkUrls": ["https://example.com/game.apk"]
}
```

The interface accepts 1 through 1,000 HTTP(S) addresses in list order. Each list entry gets a
separate workspace, including repeated addresses or packages. URL userinfo, fragments, and
whitespace are rejected. The input file is limited to 4 MiB. No HTTP server or model call is
involved.

```powershell
uv run game-auto preprocess --input var/apks.json
uv run game-auto runs --limit 20
uv run game-auto status <run_id>
uv run game-auto trace <run_id> --follow
uv run game-auto resume <run_id> --input var/apks.json
```

Keep the input file private and available for recovery. The framework stores ordered URL digests,
not URL copies. The plugin reads the caller-owned file and verifies the requested URL's digest
before use. Query credentials are never copied into recorded RPC inputs, traces, or checkpoints.
Changed URLs or list order require a new batch; `--input` may point to a moved file with the same
list. An optional `--key` deduplicates the submitted list; each preprocess command still creates a
new execution run.

## Downloads and recovery

The kernel maintains a FIFO queue with five workers by default. With 50 URLs, at most five APKs are
being processed and the other 45 wait initially. A slot is released after download, validation, and
metadata inspection finish or the item fails. Completion order can differ from input order; status
and the final summary retain input order. `preprocessing.download_workers` can reduce the worker
count from five down to one; values outside 1 through 5 are rejected.

Each active APK has its own plugin process and download worker thread. The thread owns its HTTP
client, files, checkpoint state, and metadata inspection. File hashing, ZIP inspection, and stdio
may use auxiliary threads; the limit controls APK workers, not the total OS thread count.

Downloads stream to `download.part`. Every 8 MiB and on transfer exit, data is flushed and synced
before an atomic checkpoint records the byte count and prefix SHA-256. Recovery truncates bytes
beyond the last checkpoint and verifies the saved prefix hash. Completed APKs are hash-checked
before local reuse. A local hash mismatch blocks the item rather than silently trusting the file.

Resumption uses Range and If-Range only with a strong ETag. Partial responses must agree on offset,
total length, ETag, and the final resource URL digest. A full response replaces the partial file;
changed resources or rejected ranges restart from zero. Servers without strong validators restart
instead of appending uncertain bytes. Transient network failures, HTTP 408/429, and server errors
use bounded retries. Other failures block the item; remaining items continue.

Queued items appear as `queued`, then `running`, `ready`, `blocked`, or `incomplete`. The kernel
reserves an individual span at enqueue time; interleaved progress and errors are matched by span
rather than by the most recently started APK. Single-item failures release their slot for the next
queued entry. Cancellation, trace persistence failure, or an unexpected kernel error stops new
dispatch and cancels the other workers before the run is finalized.

Ctrl+C leaves preprocessing blocked and resumable. The explicit `cancel` command is terminal.
Completed transfers awaiting archive inspection can finish locally on resume without another GET.

ZIP structure, manifest presence, and entry CRCs are checked before atomic promotion to
`application.apk`. The default size limit is 8 GiB; archive inspection also bounds entry count and
declared expansion size. SHA-256 identifies local bytes, not publisher authenticity. No signature
verification is claimed. Limits and deadlines are in the frozen preprocessing configuration.

## Metadata and isolation

Android SDK Build Tools must provide AAPT2. Resolution uses `GAME_AUTO_AAPT2`, the existing
`AAPT2_CMD` override, PATH, or SDK build-tools directories. Tool invocation uses an argument array.
The implementation follows the local utility's AAPT2 approach without importing that utility or
requiring its installation directory.

| Field                        | Meaning                                                                 |
| ---------------------------- | ----------------------------------------------------------------------- |
| `apkName`                    | Default application label; package name if no default label is reported |
| `packageName`                | Manifest package identifier                                             |
| `versionName`, `versionCode` | Values reported by AAPT2, or null when absent                           |
| `sizeBytes`, `sha256`        | Downloaded file size and content identity                               |

Each workspace is `var/runs/<run_id>/apks/apk_<index>/`, containing `application.apk`,
`download.json`, `metadata.json`, and `work/tmp/`. A process handles one APK; work and temporary
directories are separate for every item. This is filesystem and process isolation, not an OS sandbox
or an isolated Android device. Remote filenames and package names never choose local paths.

An OS-owned `workspace.lock` covers the complete item operation. A second worker cannot rewrite the
same workspace while an earlier process still owns it, including during recovery after an
interrupted host. Worker events cross to the plugin's RPC event loop through thread-safe futures;
only that loop uses the stdio peer. SQLite and kernel state changes stay on the kernel event-loop
thread. Download threads never share a database connection or another APK's mutable state.

The kernel validates metadata, writes each `metadata.json`, and publishes
`artifacts/preprocessing.json` as the batch summary. Status includes per-item results and the latest
recorded progress. Calls, retry attempts, checkpoints, and AAPT2 output remain inspectable through
the existing trace commands and offline HTML export. Failed trace persistence stops further work.

References: [HTTP range semantics](https://www.rfc-editor.org/rfc/rfc9110.html#name-range),
[HTTPX streaming](https://www.python-httpx.org/async/#streaming-responses), and
[AAPT2](https://developer.android.com/tools/aapt2).
