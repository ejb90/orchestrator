# Operations and recovery

## Deployment baseline

This first pass assumes a per-user Linux coordinator on a persistent machine with durable
local storage. Put `.orchestrator/` (or `--root`) on that machine's local filesystem. The
run database remains there; task/workflow [directory bases](directories.md) can place
attempts on other local/shared storage. Do not put SQLite on an HPC shared/network filesystem. Local compute requires
`--allow-local`, or `allow_local=True` through the Python API.

Use a new run for new inputs or modified workflow definitions. Resume reads the original
specification from SQLite and verifies its digest; it does not reload the Python or YAML definition.
Task and policy code must remain importable and compatible. Callable source hashes and
package version pinning per run are not yet enforced; avoid changing their code during a run.

## CLI reference

CLI help uses Rich Click with coloured options, readable argument descriptions, and
command panels for workflow definitions, execution, and run inspection:

```bash
uv run orchestrator --help
uv run orchestrator plan -h
uv run orchestrator run --help
```

`-h` and `--help` work on the main command and subcommands. Colour is detected from the
terminal; set `NO_COLOR=1` to disable it. Run states also use colour (green success,
red failure, yellow waiting/cancellation, magenta uncertainty). The status text remains
visible without colour. Trees and tables use coloured headings and node labels.
JSON output, raw logs, and retained-script output remain suitable for piping; diagnostics
and the run ID announcement stay separate from JSON stdout.

Use `uv run orchestrator --help` and command-specific `--help`. Global `--root PATH` goes
before the subcommand; its default is `.orchestrator` relative to the current directory.
Definition commands accept Python files and `.yaml`/`.yml` [static definitions](yaml.md).

| Command | Purpose |
| --- | --- |
| `validate FILE --inputs '{...}'` | Compile and check definitions without submitting |
| `plan FILE --inputs '{...}' [--tree] [--format json\|table\|tree]` | Print JSON (default), a Rich dependency table, or a prerequisite tree; choose one output option |
| `render FILE --output DIR` | Write script previews without submitting |
| `script RUN_ID NODE_ID [--attempt N]` | Verify and print a retained execution script |
| `run FILE --allow-local --max-active 4` | Create and coordinate a new run |
| `status RUN_ID [--json]` | Read the latest persisted snapshot without taking ownership |
| `inspect RUN_ID NODE_ID` | Print node state and all attempt metadata as JSON |
| `logs RUN_ID NODE_ID [--stderr] [--attempt N]` | Print the chosen attempt's logs; default is latest stdout |
| `events RUN_ID [--json]` | Inspect ordered state/action history |
| `cancel RUN_ID` | Request cancellation; does not claim it is already complete |
| `resume RUN_ID [--json]` | Acquire ownership, reconcile, then schedule from stored state |

`run`, `resume`, and `status` support `--json`; `inspect` always produces JSON.
`plan` defaults to JSON and supports `--format table` for a readable
[dependency view](dags.md#terminal-dependency-table), or `--format json` explicitly.
Use `--tree` (or `--format tree`) for a compact [prerequisite tree](dags.md#terminal-dependency-tree).
The run ID announcement goes to stderr, leaving JSON stdout usable by tools. Execution
returns exit code 0 on success, 1 on failure/cancellation, and 2 when unresolved uncertainty
needs attention. Inspection succeeds regardless of the run's state.

```bash
uv run orchestrator --root /durable/local/runs run examples/diamond.py --allow-local --json
uv run orchestrator --root /durable/local/runs status RUN_ID --json
```

A workflow can also be compiled and run without the CLI:

```python
from pathlib import Path

from examples.diamond import workflow
from orchestrator import Coordinator, compile_workflow, create_run

spec = compile_workflow(workflow)
path = create_run(spec, Path(".orchestrator"), allow_local=True, max_active=2)
state = Coordinator(path).run()
print(state["status"], state.get("outputs"))
```

## Files and history

```text
ROOT/RUN_ID/
  state.sqlite             coordinator-owned snapshot + append-only events
  state.sqlite-wal         SQLite transaction data while open
  spec.json                human-readable input snapshot (execution uses SQLite copy)
  coordinator.lock         exclusive local process ownership
  cancel.request           durable cancellation request
  attempts/WORKFLOW/NODE/ATTEMPT_ID/
    input.json             original resolved arguments, task, and identities
    job.sh                 retained execution script (job.zsh or job.fish for those shells)
    source.script          captured original script source
    script.json            script digest, interpreter, backend, and submission arguments
    staged-inputs.json      inputs after artifact copy/link staging
    runner.json            PID + Linux boot ID/start ticks + attempt identity
    stdout.log
    stderr.log
    outputs.json           optional command-authored output dictionary
    result.json            validated runner-owned terminal report
```

`job.*`, `source.script`, and `script.json` are present for script tasks. Inspect the
retained script with `orchestrator script RUN_ID NODE_ID`; the command verifies its
digest and metadata against the durable submission intent before printing it.

??? info "Theory: A transaction keeps history and state together"

    The JSON snapshot in SQLite retains every attempt and committed decision. Events and the
    corresponding snapshot revision commit in one transaction. This is a small-run design:
    each transition rewrites the complete snapshot. It is not tuned for very large graphs.

    Read more: [Transactions and external actions](../fundamentals/durability.md#transactions-have-a-boundary).
Keep SQLite sidecar files with the database and use SQLite-aware backups, or back up while
the coordinator is stopped. Do not copy only an active database file and assume it is complete.

## Interrupt, resume, and cancel

Ctrl-C interrupts the coordinator without killing detached runners. A later `resume`:

1. Takes an exclusive local process lock.
2. Loads and validates the stored specification and snapshot.
3. Ingests matching result manifests or checks live process identity.
4. Schedules newly eligible nodes, preserving completed nodes and existing attempts.

Repeated resumes of a completed run do not create more attempts. A matching result
can still be accepted after its launch acknowledgement was lost. Mismatched result
identities are rejected and surfaced as uncertainty.

??? info "Theory: A process ID is not a durable execution identity"

    A PID may be reused after a process exits or a host restarts. Local observation also
    checks boot identity and process start ticks. Results carry run, node, and attempt IDs
    so a delayed or repeated report cannot authorize another execution or replace a newer
    attempt's outputs.

    Read more: [Identity and reconciliation](../fundamentals/durability.md#identity-makes-observations-meaningful).

`cancel` leaves a durable request. An active coordinator skips unstarted ordinary work,
sends SIGTERM to known active local process groups or calls `scancel` for acknowledged
active Slurm jobs, and waits for observations. A finished task
may still be recorded as succeeded if it won the race with cancellation. Required finalizers
remain eligible and are not sent cancellation signals. Without an active coordinator,
use `resume` to process the request. `cancel` on a terminal run is a no-op. For Slurm,
lost job acknowledgements and scheduler kills without a result can remain uncertain;
see [Slurm recovery limits](scripts.md#slurm-submission-experimental).

## Diagnosing uncertainty and failure

A submission intent commits **before** launching the runner. If the coordinator loses the
local launch acknowledgement, it looks for the runner record or result instead of submitting
again. Missing identity and missing result cannot establish whether a task ran. After a
short launch grace period, that attempt becomes `uncertain`; it is never automatically retried.
Other independent work may continue. When only uncertain attempts remain active and no
immediate scheduling progress is possible, the coordinator returns with run status `uncertain`.

??? info "Theory: Why an unacknowledged submission is uncertain"

    The coordinator can crash after a backend accepts work but before the acknowledgement
    is committed. The database and scheduler do not share an atomic transaction. Repeating
    the action without evidence could run the same side effects twice, so reconciliation
    must establish the original attempt's outcome first.

    Read more: [The submission crash window](../fundamentals/durability.md#transactions-have-a-boundary).

Inspect the attempt, its stderr, and its manifest directory. Correct task/environment or
artifact problems before deciding what to do. A later resume can accept a valid result that
arrived late. This first pass has **no force-resubmit or manual resolution command**. If
completion cannot be established, preserve the run for diagnosis; a deliberate new run may
duplicate side effects. Do not edit SQLite to mark success or invent a result manifest.

Ordinary task exceptions and nonzero command exits are `application` failures; timeout
reports use `timeout`. Retry policies apply only to those declared categories, up to their
attempt limit and with fixed backoff. Failure after exhausted retries blocks ordinary
descendants, allows terminal-triggered finalizers, and fails the scope. Inspect stderr for
the task traceback. CLI status and events are coordinator diagnostics; stdout/stderr are
separate task logs. Rotating diagnostic log files and scheduler usage metrics are deferred.

??? info "Theory: Cancellation is a request, not an observed outcome"

    Work may finish before a signal or scheduler cancellation takes effect. The
    coordinator records the observed result, while required finalizers remain eligible
    under their trigger rules. Cleanup still needs a live coordinator or a later resume.

    Read more: [Cancellation and finalization](../fundamentals/durability.md#cancellation-and-cleanup-also-need-evidence).

## Specification compatibility

New workflow specifications use schema version 4, including captured scripts and directory settings.
Version 3 runs without directory configuration remain supported in their original layout.
Versions 1 and 2 are rejected on resume rather than silently reinterpreted. Retain the
matching previous code to finish older runs, or start a new run; the SQLite storage
schema remains version 1. Automatic schema migration is not implemented.

YAML authoring has its own `version: 1`, which compiles to workflow schema v4. It does
not identify a v1 persisted run. Python and YAML runs share the same compatibility and
recovery rules; see [YAML definitions](yaml.md) for supported authoring options.
