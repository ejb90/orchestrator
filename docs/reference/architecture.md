# Architecture and first-pass status

For the design philosophy and basic computer science, start with
[Fundamentals](../fundamentals/index.md). This reference records the implemented module
boundaries, state transitions, deployment assumptions, and remaining work.

## Deployment and reuse decision

The working baseline is one Linux coordinator per run, per-user ownership, local SQLite,
local attempt storage, and explicitly permitted local subprocess compute. It targets small
and medium static graphs whose task runtime dominates scheduling overhead. This is a
provisional development deployment, not a claim about the eventual cluster environment.
Task/workflow directory bases can place attempts outside the durable local database root; see [execution directories](../guide/directories.md).

The initial backend is direct subprocess execution. Parsl already provides dataflow
scheduling and reusable execution providers; its HighThroughputExecutor uses pilot
allocations and worker communication. Wrapping it could later reduce executor plumbing,
but would still require an adapter for this project's durable attempt identities,
submission uncertainty, and scope completion. The current thin backend keeps those
contracts visible while they stabilize. This is a documentation-level comparison, not a
Parsl prototype or benchmark. See the [Parsl execution overview](https://parsl.readthedocs.io/en/stable/userguide/overview.html).

SQLite is restricted to a single coordinator host with reliable local locking. A shared
filesystem database is not the baseline; see SQLite's discussion of
[network filesystem limitations](https://www.sqlite.org/useovernet.html).
Script tasks now have explicit local or experimental Slurm placement. See the
[script guide](../guide/scripts.md) for shared-filesystem requirements and recovery limits.

## Components

| Module | Responsibility |
| --- | --- |
| `api.py` | Reusable task/workflow definitions, invocation handles, bindings, finite loop helper |
| `spec.py` | Schema v4, JSON serialization/digest, validation, deterministic scope expansion |
| `yaml_loader.py` | Version-1 static YAML definitions translated into the Python builders and common compiler |
| `engine.py` | Pure readiness rules, decisions, scope completion, retry/cancellation/recovery loop |
| `backends.py` | Execution protocol, local adapter, and experimental Slurm routing |
| `scripts.py` | Source capture, shell rendering, directive validation, retained hashes |
| `runner.py` | Import/command invocation, process identity, staged manifests, atomic reports |
| `artifacts.py` | Object contracts at runtime: copy/link inputs, output validation and collection |
| `storage.py` | SQLite snapshot/events, revision guard, local exclusive ownership |
| `paths.py` | Absolute/root/parent execution bases, pinned run mappings, isolated attempt locations |
| `cli.py` | Python/YAML definition loading, user commands, Rich output, machine-readable inspection |
| `presentation.py` | Static Rich dependency tables and prerequisite trees over the compiled specification |

??? info "Theory: Graph scopes and a single orchestration authority"

    The compiler expands child invocations and keeps their enter/exit scope barriers. Data
    references add dependencies. A workflow exit waits on direct task and child-workflow exits,
    which transitively wait for their descendants. Runtime policies see only their resolved,
    declared inputs. Decisions record input evidence and the preceding snapshot revision.
    The coordinator owns all orchestration writes; runners only publish filesystem observations.

    Read more: [Policy, mechanism, and evidence](../fundamentals/philosophy.md#separate-policy-from-mechanism).

Storage uses a versioned JSON snapshot plus append-only events. Workflow schema v4 and
legacy v3 runs without directory configuration are supported; SQLite remains schema v1.
Other unsupported versions are rejected. There are no automatic schema migrations.

??? info "Theory: Transactional history and local ownership"

    Node states, attempts, intents, and decisions are separate records within one
    snapshot rather than separate relational tables. Snapshot updates and events commit
    together with a revision check. A local file lock prevents simultaneous coordinators
    for the supported host; it is not a distributed lease or fencing protocol.

    Read more: [State transitions and transaction boundaries](../fundamentals/durability.md#transactions-have-a-boundary).

## Transition semantics

The first pass stores a reduced state machine: `ready` is derived by a pure function,
not persisted. Scope nodes use the same terminal labels but different completion rules.
Every persisted node transition appends a `node_transition` event with a reason.

| Entity / from | Prerequisite | To / event | Follow-up |
| --- | --- | --- | --- |
| Task `waiting` | Dependencies/guard ready, capacity and retry backoff permit | `active` / `submission_intent` | Launch outside the transaction |
| Attempt `submission_pending` | Launch returns PID and identity | `running` / `submission_acknowledged` | Observe runner/result |
| Attempt pending/running | No trustworthy result or live identity after grace period | `uncertain` / `submission_uncertain` | Reconcile; never resubmit blindly |
| Attempt uncertain | Matching live identity appears | `running` / `attempt_running` | Continue observing |
| Attempt active/uncertain | Matching valid terminal result | `succeeded`, `failed`, or `cancelled` | Update owning task atomically |
| Task `active` | Successful validated result | `succeeded` | Publish outputs, release consumers |
| Task `active` | Retryable failure, attempts remain | `waiting` with retry deadline | New isolated attempt after backoff |
| Task `active` | Terminal failure or confirmed cancellation | `failed` / `cancelled` | Block ordinary consumers, allow terminal triggers |
| Ordinary node `waiting` | Wrong branch, failed required predecessor, or scope closing | `skipped` | Record explanation; never launch |
| Decision `waiting` | Required evidence succeeded | `succeeded` or `failed` plus committed decision | Branch, close scope, fail, or request run cancellation |
| Scope exit `waiting` | All direct children terminal | `succeeded`, `failed`, `cancelled`, or `skipped` | Bind declared or early-completion outputs |
| Run `running` | Every node terminal | Root outcome | Persist output contract |
| Run `running` | Only unresolved attempts remain active; no immediate progress | `uncertain` | Return control for diagnosis/resume |

Terminal nodes are not reopened. Cancellation requested and signal sent are separate
attempt flags, not proof of cancellation. A successful result can win a cancellation race.
A closing scope skips unstarted ordinary descendants, drains submitted work, and waits for
required finalizers. Failed required work always prevents successful scope completion.

The backend protocol returns observations rather than changing domain state. Submission,
observation, and cancellation are isolated from scheduling. It is intentionally minimal:
batched scheduler polling, richer status categories, reconciliation deadlines, capability
negotiation, and a pluggable storage protocol are still follow-up work.

## Milestone coverage

| Plan milestone | First-pass delivery | Remaining qualification or work |
| --- | --- | --- |
| 0 Deployment / reuse | Documented local baseline and Parsl comparison | Confirm actual host, filesystem, scale, cluster requirements; executor prototype if useful |
| 1 Spec / compiler | Versioned deterministic graph, reusable definitions, nesting, references/cycles and basic conditional validation; static YAML frontend | Rich input/output schemas; deeper policy availability analysis; advanced YAML composition |
| 2 Local execution | Real subprocesses, manifests, output validation, dependency failure handling | Broader resource enforcement and non-Linux support |
| 3 Durable coordination | Transactional snapshots/events, intents, ownership, retry isolation, identity-based recovery | Normalized storage interface, manual uncertainty resolution, stronger host-loss durability |
| 4 Controls | Branch joins, bounded single-task iteration, successful scoped early finish, required task finalizers | General iteration-body convenience API, optional cancellation of draining scope work |
| 5 Slurm | Retained scripts, explicit placement, sbatch/squeue/scancel adapter, mocked tests | Site profiles, accounting, lost-ack job discovery, real cluster smoke test |
| 6 Usability | CLI, runnable examples, MkDocs, tests, locked dependencies, Ruff and mypy, GitHub Actions checks | Release packaging qualification, full code/environment provenance, cluster operations guide |

No dynamic graph expansion, caching, pilot allocations, arrays, MPI/GPU
resource contracts, distributed ownership, remote data transport, or automatic
environment provisioning is implied. SQLite stores each full snapshot, so this is not a
high-throughput scheduler. The API and schema are initial and may require migration as
the remaining milestones are implemented.

YAML format v1 covers flat static graphs with reusable tasks, bindings, dependencies,
resources, retries, and finalizers. Nested workflows and conditional control remain
available through Python. YAML uses the same compiled schema v4 and stored-run protocol.

## Verification and next acceptance steps

The automated suite exercises real local execution, nested output bindings, diamond graphs,
branch joins, step-four convergence, finalizer failure, retry isolation, timeouts, nested
concurrency limits, live-process restart, cancellation, duplicate terminal resume,
stale result identity, lost acknowledgement with and without a result, ordered events,
validation errors, CLI operations, and exclusive ownership.
YAML coverage includes equivalence with Python specifications, CLI rendering and execution,
definition-relative paths, source capture, and rejection of malformed static definitions.
The [development guide](development.md) lists the local checks and GitHub Actions configuration.
`plan --format table` presents the complete compiled graph in topological order,
including scope barriers and control settings. The default JSON interface is preserved.
`plan --tree` offers a compact prerequisite tree, collapsing barriers and redundant edges
and marking shared nodes without changing execution semantics.

Tests run against actual SQLite and local subprocesses; submission crash injection uses
a small fake backend. Local testing does not establish Slurm compatibility. Before Slurm
deployment, settle the coordinator host and storage layout, shared artifact filesystem,
account/partition restrictions, installed task environments, accounting availability, and
MPI/GPU needs. Scheduler tests use mocks; add accounting/crash/race coverage and a real cluster smoke
test before production use. The original plan remains the source for the broader release acceptance criteria.

Task input/output objects now support local filesystem copy/link staging, required and
optional artifacts, explicit output collection, and importable output validators. This
does not provide remote file transfer or change workflow decision ownership.
