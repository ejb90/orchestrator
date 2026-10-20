# Python HPC workflow orchestrator: initial project plan

Status: implementation baseline with script-first execution requirements  
Created: 19 October 2026  
Updated: 20 October 2026  
Target: Python 3.12+  
Project name: to be decided

## 1. Purpose

Build a Python-first workflow orchestrator for local and HPC execution. Users compose reusable tasks and nested workflows, declare data and control dependencies, assign execution resources, and inspect durable execution history. A central coordinator evaluates conditional behavior, including successful early completion of iterative workflows.

Most executable tasks run a series of terminal commands. Make an inspectable script, or job control file (JCF), the primary execution artifact: a Bash, Zsh, or Fish script that describes its interpreter, resolved resource requests, environment setup, working paths, and commands. For Slurm execution, that file includes the actual `#SBATCH` directives used for submission. Python remains the workflow-authoring API; application tasks need not be rewritten as Python callables.

The initial implementation prioritizes clear semantics, restart recovery, and explainable decisions over maximum throughput or distributed coordination.

This document records the accepted architectural direction and provides implementation work packages. Proposed defaults are identified where deployment details still require validation.

Implementation status (20 October 2026): `Task(script=Script(...))` now supports captured inline/file sources, Bash/Zsh/Fish rendering, environment setup, per-attempt retained job files and hashes, the existing staging/validation lifecycle, and CLI render/script inspection. Schema v3 records script definitions. An experimental shared-filesystem Slurm adapter submits the retained file and observes/cancels jobs; scheduler tests are mocked, not cluster-qualified. Argument-list and Python tasks remain supported. Site profiles, hierarchical scheduler overrides, accounting/lost-ack discovery, separate storage roots, dependency snapshots, and real cluster qualification remain pending. Fish uses explicit authored failure checks because it has no Bash-style errexit; see `docs/guide/scripts.md` for implemented semantics and limits. The sections below retain the broader target design where these items are not yet implemented.

Documentation status (20 October 2026): the user guides now cover Python and YAML
entry points, reusable task definitions versus named steps, workflow output bindings,
script preview/inspection, source capture, environment behavior, path resolution, and
current Slurm recovery limits. The task-contract migration section is corrected to
workflow schema v3 (SQLite v1); YAML authoring version 1 is explained separately.
The documentation home, quick start, README, and navigation link the YAML and new
development/CI guides. CI setup and remaining hosted/cluster qualification are documented.
Verification: the strict MkDocs build passes, all three YAML guide examples compile and
execute successfully (including reuse with isolated outputs), and the documented five
CI commands match the workflow file.

## 2. Initial scope

The first release supports:

- A Python API that builds a versioned, serializable workflow specification without executing it.
- Reusable executable tasks and nested workflows with named inputs and outputs.
- Script-based tasks as the primary application interface, retaining a self-describing execution script for every attempt.
- Explicit Bash, Zsh, and Fish interpreter selection; Slurm scripts with resolved scheduler directives, environment initialization, and application commands.
- Serial and parallel composition over explicit dependencies.
- Static graphs, bounded iteration, conditional branches, and successful early stopping.
- Local subprocess execution and Slurm execution, initially one job per executable task.
- One active coordinator per run, one cluster per run, and a shared artifact filesystem.
- Persistent state, attempt history, retry policies, cancellation, and restart reconciliation.
- A CLI for validation, planning, execution, inspection, logs, cancellation, and resumption.
- Structured events, diagnostic logs, task output, and provenance.

Basic YAML authoring is implemented for flat static graphs. Advanced YAML composition (nested workflows, decisions, and iteration), arbitrary dynamic graph expansion, unbounded loops, pilot allocations, job-array optimization, multi-cluster execution, distributed coordinators, caching across runs, a web interface, and automatic data staging between unrelated filesystems remain deferred.

The architecture must accommodate these features without pretending they are already supported.

## 3. Architectural principles

1. Separate definitions, runs, attempts, and backend jobs.
2. Separate orchestration policy from execution mechanisms.
3. Make state changes durable and explainable.
4. Give the coordinator exclusive authority over orchestration state.
5. Treat observations from workers and schedulers as potentially delayed, repeated, or incomplete.
6. Keep decision policies deterministic over an explicit snapshot and declared dependencies.
7. Validate specifications before submission.
8. Preserve uncertainty instead of guessing that missing work succeeded or failed.
9. Prefer small interfaces and composition over inheritance-heavy frameworks.
10. Do not promise exactly-once execution or atomic transactions across the database and Slurm.

## 4. Domain model

| Entity | Responsibility |
| --- | --- |
| TaskDefinition | Describes a script-based task, or an argument-list command/importable Python operation, with input/output contracts and defaults. |
| ScriptDefinition | Records the script body or source file, shell/interpreter, environment setup, and imported or structured scheduler requests. |
| ExecutionScript | Immutable rendered script/JCF for one attempt: resolved headers, environment, paths, lifecycle commands, application commands, and content digest. |
| TaskInput | Named input object with source binding, optional staging destination, copy/link mode, kind, and essential/optional contract. |
| TaskOutput | Named output object with source/destination, kind, copy/link collection mode, essential/optional contract, and validation logic. |
| WorkflowDefinition | Composes task and workflow invocations, dependencies, control rules, and output bindings. |
| NodeInvocation | Places a definition in a workflow with a unique identity, input bindings, and overrides. |
| WorkflowSpec | Versioned representation produced by the Python builder and consumed by the engine. |
| Run | One execution of a specification with particular inputs and configuration. |
| NodeRun | Runtime state for a task, decision, or workflow scope within a run. |
| Attempt | One execution attempt for an executable task, with isolated paths and backend metadata. |
| ArtifactRef | Reference to a file or directory output, with declared validation and optional metadata. |
| Decision | Recorded policy outcome, including its scope, evidence, and reason. |
| ExecutionBackend | Submits, observes, reconciles, and cancels executable work. |
| StateStore | Persists state, events, action intent, and ownership information transactionally. |

Reusable definitions must remain distinct from invocations: the same definition can appear multiple times with different inputs and resource settings. Retries create attempts, not new task definitions.

## 5. Definition and compilation

The data flow is:

```text
Python builder ──┐
                ├── WorkflowSpec → validation → compiled graph → coordinator
YAML v1 ────────┘
```

The builder creates data structures and performs no submission. Python may generate repeated structures at definition time. Runtime conditions must use explicit control constructs rather than ordinary Python branching over unfinished results.

Use versioned script definitions and importable, versioned Python task/policy references with serializable arguments. Do not initially support arbitrary closures, captured interpreter state, or automatic serialization of live Python objects. Scripts can be supplied as source files or literal bodies; capture their content and digest when creating the run so a later edit to the source file cannot silently change a retry or resumed run.

The specification should include a schema version, definition references, captured script sources and interpreter choices, invocation identities, input/output bindings, dependencies, resource defaults, retry policy, control constructs, and workflow output contracts. Persist the resolved specification and its digest for each run.

Compilation resolves the graph and declared configuration without executing application commands. A separate rendering step produces the final attempt script after attempt identity, paths, input bindings, resources, and placement are known. Persist the script and its digest before recording the submission action. Planning may show a preview with clearly marked unresolved attempt fields; a submitted script must contain no unresolved orchestrator placeholders.

Validation must detect cycles, duplicate identities, missing references, invalid output bindings, unsupported resource settings, invalid conditional joins, and policies that depend on unavailable results. Future static formats must pass through this same validation pipeline; YAML must not become a second execution engine.

### Nested workflows

Expand nested workflows into one execution graph while retaining hierarchical identities and scope boundaries. A workflow is a logical composition and does not occupy a worker while waiting for children.

Nested workflows expose named inputs and outputs. Callers do not reference internal nodes. A child workflow can finish successfully early without stopping its parent. Parent behavior follows the child's declared completion and output contract.

Serial and parallel helpers construct dependencies. They are not separate scheduler implementations. The compiler should preserve a mapping from expanded nodes to their original workflow invocations for diagnostics and visualization.

## 6. Data and execution contracts

### Task forms and script authoring

Support three explicit task forms, with scripts as the primary authoring path:

- **Script tasks:** an existing script file or a literal multiline script body, with an explicitly declared shell/interpreter. Scripts contain ordinary command sequences, pipelines, environment initialization, and application-specific control flow.
- **Argument-list commands:** retain this convenient form for single executable invocations; never implicitly interpret argument strings as shell code.
- **Importable Python callables:** retain these for tasks that are naturally Python operations, through the installed task-runner entry point.

Script tasks must not require users to express each terminal command as a separate workflow node. One node can perform preparation, simulation, and post-processing in one script when they share execution resources and retry semantics. Users split those steps into separate nodes when they need independent dependencies, resources, retries, or reusable outputs.

Support two script-source cases:

1. A command body plus structured resource/environment configuration, from which the renderer creates the complete script.
2. An existing self-contained shell or Slurm script. Capture the original source, import supported static header settings into the task defaults, and render a resolved execution copy. Preserve the authored command body and retain the original alongside the generated version.

Do not maintain two competing sources of scheduler configuration. Supported imported `#SBATCH` values become task defaults and participate in the same precedence rules as structured settings. Conflicting declarations at the same level, unsupported directives, or directives placed after executable statements must produce a validation error. Any authorized override must appear in the final rendered header and provenance; do not silently add conflicting `sbatch` flags.

### Self-describing execution scripts / JCFs

Retain the exact executed/submitted bytes as `job.sh`, `job.zsh`, or `job.fish` in the attempt directory. The extension is descriptive; the resolved shebang/interpreter is authoritative. Include the following information in the file itself:

| Section | Required content |
| --- | --- |
| Interpreter | Explicit shebang, shell executable, and declared invocation options. |
| Scheduler configuration | For Slurm: resolved partition/account/QoS where configured, nodes/tasks/CPUs, memory and wall time, optional GPU requests, job/attempt identification, working directory, and stdout/stderr paths. |
| Provenance | Run, hierarchical node, and attempt identifiers; source/specification digests; selected backend/site; paths to input, staged-input, and result manifests. Store the final script digest separately to avoid a self-referential hash. |
| Environment | Explicit inheritance policy, declared variables, PATH changes, shell-specific module initialization/load commands, environment activation, and executable paths or launchers. |
| Filesystem setup | Resolved working directory, required directory creation, input source/destination and copy/link contracts, output collection locations, and validation declarations. |
| Lifecycle | Visible staging and result-publication commands or documented versioned helper calls, failure propagation, and signal/cleanup handling supported by the selected shell. |
| Application | The actual ordered commands and pipelines, including explicit `srun` or other launchers when requested. |

The script must explain execution without requiring someone to inspect the coordinator database. If versioned runner helpers or external environment files are required, identify them and their arguments explicitly; include readable input/output contract summaries rather than only an opaque manifest path. Self-describing does not mean bundling the application, data, or interpreter into one file. Record those external dependencies and any runtime-generated values that cannot be known at render time.

Slurm reads `#SBATCH` directives before shell execution and does not expand shell variables in them. Put the shebang first and the complete directive block before the first executable statement. Resolve orchestrator variables in directives before submission; distinguish supported Slurm filename tokens such as `%j` from shell expansion. Validate directive values separately from shell argument quoting.

Render or validate each shell explicitly. Bash/Zsh/Fish differ in variable assignment, activation scripts, pipeline status, and signal handling. Do not translate arbitrary shell programs or put Bash-only setup into a Fish script. Generated multi-step scripts must propagate failures; define and test per-shell behavior rather than assuming `set -e` catches every failure. Preserve explicit application error handling in authored scripts and document its effect on the final exit status.

A representative **application-level Slurm JCF** is shown below. Paths, allocation settings, modules, and executables are illustrative site choices. This sketch shows the readable application/configuration portion; a managed rendering must additionally include the visible staging/finalization helper calls and attempt/result protocol described above. It is not a currently generated or cluster-tested script.

```bash
#!/bin/bash
#SBATCH --job-name=optimize-step-1
#SBATCH --partition=compute
#SBATCH --account=research
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=16G
#SBATCH --time=01:00:00
#SBATCH --chdir=/shared/project/runs/run-123/step-1/attempt-1
#SBATCH --output=/shared/project/runs/run-123/step-1/attempt-1/stdout.log
#SBATCH --error=/shared/project/runs/run-123/step-1/attempt-1/stderr.log
#SBATCH --comment=orchestrator:run-123:step-1:attempt-1

# Run: run-123; node: optimization/step-1; attempt: attempt-1
# Input: /shared/project/data/config.toml -> inputs/config.toml (copy, essential)
# Output: results/summary.json -> artifacts/summary.json (copy, essential)
# Validation: application.validators:valid_summary
# Inheritance: selected site variables; application PATH and thread count set below.
set -euo pipefail
source /etc/profile.d/modules.sh
module purge
module load simulation/1.2
export PATH="/opt/simulation/1.2/bin:/usr/bin:/bin"
export OMP_NUM_THREADS=8
cd /shared/project/runs/run-123/step-1/attempt-1

# Managed input staging precedes these application commands.
prepare-input --config inputs/config.toml --output prepared.dat
srun simulate --input prepared.dat --output results
summarize --input results --output results/summary.json
# Managed validation, collection, and result publication follow successful execution.
```

Create working/log directories before submission: a script cannot create a directory that Slurm already needs to open its log files. Resource requests describe the requested allocation, not every scheduler/site default or the eventual granted environment. Capture scheduler observations separately.

### Input/output object contracts and lifecycle

Exchange small values through validated serialized records. Exchange large data through artifact references. Store artifact metadata and references in the database, not large arrays or scientific datasets.

Represent task inputs and outputs as lists of class instances (`TaskInput` and `TaskOutput`), with unique names for binding. Inputs declare sources, staging destinations, copy/link mode, and whether they are essential. Outputs declare sources, collection destinations, essential/optional behavior, and importable validation logic. Output logic validates or collects the output; branching, scope completion, and early stopping remain coordinator policies.

Every attempt receives its own working directory, original and staged input manifests, retained execution script, stdout/stderr paths, and result manifest. Stage essential inputs before application commands; a missing essential input prevents execution. Validate essential outputs after successful commands and before publishing results. Optional missing outputs are represented explicitly through the output contract and handled by consumers. Copying is the default; symbolic linking is an explicit choice with shared-data semantics.

The script and runner must use a single lifecycle implementation: do not stage twice or have both a wrapper and coordinator race to publish results. A script may call installed, versioned helpers for staging, validation, collection, and atomic result publication, but those calls must be visible and documented. Workers never modify coordinator state or independently schedule descendants. Shell traps are best-effort; scheduler termination or host loss can prevent finalization and must remain reconcilable.

A proposed initial success rule is: execution completed successfully, a valid result record exists, and required output validation passed. A shell exit code alone is insufficient for managed success. Reconcile temporary disagreement between scheduler and result observations before declaring a final outcome.

Use a documented atomic publication protocol for manifests on supported shared filesystems, such as writing a temporary file and renaming within the same filesystem. Validate deployment filesystem guarantees; atomic visibility does not itself guarantee durability.

Initially assume shells, task code, runner helpers, and dependencies are installed and accessible to compute nodes. Record the selected executable or environment, package/version information where available, code revision, parameters, resource configuration, and captured script content. Snapshot additional user-authored sourced files where supported; otherwise record their paths/digests as external dependencies. Automatic environment provisioning and container integration can follow later.

## 7. Resources, placement, and environments

Represent these independently:

| Configuration | Examples |
| --- | --- |
| Resource requirements | CPUs, memory, GPUs, wall time, nodes, tasks per node, CPUs per task |
| Placement | Local execution or a named Slurm site |
| Site profile | Partition, account, QoS, submission paths, module setup |
| Execution environment | Shell/interpreter, Python executable where needed, module initialization/load sequence, activation commands, environment variables, PATH and inheritance policy |
| Orchestration limits | Maximum active tasks or submitted jobs within a scope |

Specify units and distinguish total memory from memory per CPU. Validate backend capabilities instead of silently ignoring unsupported requests. MPI-related requirements need an explicit launcher contract; they must not be inferred from CPU counts.

Proposed precedence: site defaults, outer workflow defaults, inner workflow defaults, task-definition defaults (including supported imported script directives), then task invocation overrides. Resolve configuration before execution and retain the resolved values in the specification and rendered script. Limit site-specific options to a validated backend-specific extension rather than spreading Slurm fields throughout the domain model.

Define environment inheritance explicitly: a documented inherited environment or a clean/allowlisted base, followed by declared modifications and shell-specific setup. Retain the effective non-secret values needed to explain the run, and identify runtime-supplied variables separately. Do not dump credentials into the script or provenance; record required secret variable names/providers without their values. Sourced environment/module setup files are external dependencies, not an implicit interactive login-shell state.

The rendered script is authoritative for requested Slurm configuration. Record the exact submission argument list and scrub or explicitly reconcile inherited `SBATCH_*` overrides so ambient submission settings cannot silently contradict the file. Scheduler-side policies may still affect effective allocation; record those observations rather than claiming the script controls the cluster.

Working-directory and path handling must be explicit. Preserve attempt isolation as the default, resolve inherited path settings before rendering, declare which directories are created by the coordinator versus the job, and validate collisions and unsupported path overrides. Any future configurable run/artifact roots must keep durable coordinator storage distinct from scratch space. Copy/link staging on an accessible filesystem is supported by the contract; automatic transport between disconnected filesystems remains deferred.

Workflow resource assignments initially provide inherited defaults and orchestration limits. They do not reserve a shared allocation. A future allocation-group or pilot backend will represent that behavior explicitly.

Define `local` as execution on the coordinator host. Require an explicit site configuration enabling local compute there, since a coordinator may run on a login or service node where substantial computation is inappropriate.

## 8. Conditional execution and bounded iteration

The initial graph remains acyclic. A bounded iteration construct expands a finite number of iteration instances, with a decision barrier between consecutive iterations.

For a six-iteration optimization:

```text
iteration 1 → decision 1 → iteration 2 → decision 2 → ... → iteration 6
```

If decision 4 detects convergence, iteration 5 never becomes eligible for submission. Remaining iteration nodes become skipped with a recorded reason. The optimization workflow succeeds and exposes the selected result from iteration 4 through its output contract.

Supported decision outcomes should include continue, select branch, finish scope successfully, fail scope, and request cancellation. A Boolean condition may select an outcome, but the outcome itself must describe the intended semantics.

Policies declare the task results or states they require. A policy comparing three candidates normally waits for all three, regardless of completion order. Snapshot evaluation should not read mutable external state or perform submission. Expensive calculations belong in executable tasks; policy evaluation uses their small result summaries.

### Proposed control rules

- Successful early completion affects the current workflow scope by default.
- Unstarted work that is no longer needed is skipped, not failed or cancelled.
- Already-submitted work drains by default; optional cancellation is a separate explicit policy.
- Once a scope is closing, no additional ordinary work becomes eligible in that scope.
- A scope is not terminal until its submitted work and required finalizers are resolved.
- Late results cannot reopen a closed branch or reverse a committed decision.
- Cleanup/finalization nodes have explicit trigger rules and remain eligible where specified.
- Failure of a required finalizer makes the scope fail even if optimization converged.
- Finalizers remain subject to coordinator availability and recovery; they are not guaranteed to run after unrecoverable infrastructure loss.

Define joins explicitly. Initial trigger rules should cover all required predecessors succeeding, all predecessors becoming terminal, and selected branches succeeding. A join must not wait indefinitely for an intentionally skipped branch. Conditional outputs must be optional or have explicit alternative bindings, and consumers must handle that contract.

## 9. Coordinator and persistence

Use one active coordinator per run. Backend jobs and task runners report observations; they do not modify orchestration state or independently schedule descendants.

The coordinator follows this cycle:

1. Collect backend and result observations.
2. Validate attempt identity and deduplicate observations.
3. Apply allowed transitions transactionally.
4. Evaluate eligible policies against a consistent snapshot.
5. Record decisions and action intents transactionally.
6. Execute external actions outside the transaction.
7. Persist acknowledgements or uncertainty for later reconciliation.

Persist current state plus an append-only event history. Full event sourcing is not required initially. State changes and corresponding events should commit together.

Suggested records include runs, node runs, attempts, artifacts, observations, decisions, events, action intents, and coordinator ownership. Record state revisions or equivalent compare-and-set guards to reject stale updates. Decision records include their input snapshot revision and evidence references.

### Storage and deployment baseline

Proposed baseline: a persistent coordinator host with durable local storage, access to Slurm submission commands, and access to the shared artifact filesystem. Use local SQLite for this single-host mode. Do not place the SQLite database on a shared network filesystem as the default deployment.

Provide a storage interface that can support PostgreSQL for service deployments. Avoid implementing a second database before the initial contracts and recovery tests stabilize.

Workers can publish result manifests on the shared filesystem, avoiding a requirement for direct database access or inbound worker connectivity. The coordinator ingests those records and owns database writes.

Prevent concurrent coordinators for a run with an ownership mechanism appropriate to the supported deployment. For the initial single-host deployment, exclusive process ownership can be enforced locally. Multi-host ownership requires leases/fencing and is a separate deployment capability. A database ownership token alone cannot prevent every stale external Slurm action; submission reconciliation remains necessary.

If only a time-limited Slurm allocation is available for the coordinator, this deployment baseline must be revisited before implementation. Ephemeral local SQLite plus occasional copying is not an adequate durable recovery design.

## 10. State and recovery semantics

Maintain separate state machines for task nodes, execution attempts, and workflow scopes. A retryable failed attempt does not immediately make its task terminal.

Proposed task states: waiting, ready, active, succeeded, failed, skipped, and cancelled. Proposed attempt states include submission pending, submission uncertain, queued, running, reconciling, succeeded, failed, and cancelled. Cancellation requested should be represented separately from confirmed cancellation.

Finalize the transition tables before implementing the engine. Every transition must define prerequisites, emitted events, and allowed follow-up actions.

Distinguish application failure, timeout, out-of-memory termination, preemption, cancellation, submission rejection, and uncertain status. Retry policies specify applicable categories, attempt limits, and backoff. No automatic resource escalation is required initially.

### Submission uncertainty

Persist an action intent and stable attempt identifier before submission. Include that identifier in scheduler-visible metadata where supported. Record the returned job identity on acknowledgement.

If the coordinator crashes after Slurm accepts a job but before acknowledgement is persisted, resume by reconciling that attempt with scheduler records. Do not blindly resubmit. If identity remains ambiguous, retain an actionable uncertain state and expose it through the CLI.

Slurm does not provide an atomic transaction with the state store. Exactly-once execution is not guaranteed. Tasks with external side effects must account for possible retries or duplication.

### Restart behavior

On restart, acquire run ownership, load the persisted specification and state, reconcile outstanding actions and active jobs, ingest unprocessed results, and only then schedule newly eligible work. Completed tasks remain completed unless the user explicitly requests a new run or an eventual supported rerun operation.

Results are tied to run, node, and attempt identifiers. A late result from a previous attempt cannot overwrite the current attempt. Duplicate reports are harmless. Cancellation requests remain pending until reconciled with actual backend outcomes; a task may finish successfully before cancellation takes effect.

The local backend also needs restart semantics. Do not rely solely on a process ID, which may be reused. Use runner records and process identity checks, and preserve uncertainty when process completion cannot be established.

Bind each submission intent to the retained execution-script path and digest. Resume reconciles the already-submitted artifact rather than rereading mutable script source or regenerating a different payload. A retry creates a new attempt script with new identity and paths from the same captured task definition. Any intentional change to commands, environment, or resources requires an explicit new definition/run or a future recorded override operation. Manual reruns of a retained script are diagnostic executions, not authorization to overwrite the original attempt's result or coordinator state.

## 11. Execution backend contracts

Define a small interface around capabilities, submission, batched observation, cancellation, and reconciliation by attempt identity. Backend operations return structured observations rather than mutating domain state.

### Local backend

Execute the retained script with its declared interpreter through the same attempt/result contract used by Slurm. Preserve the script contents, resolved environment, and launch arguments for inspection. Keep argument-list and Python tasks as explicit alternatives. Enforce configured concurrency limits, capture output, and support process-group cancellation where available. Explicitly document which resource limits are enforced and which are informational.

Local rendering identifies the backend as local and contains no active Slurm request block. An imported Slurm script requires explicit local placement and validation of scheduler-dependent commands; never silently run a cluster task on the coordinator simply because `#SBATCH` lines look like shell comments. Shell/interpreter availability and shell-specific failure behavior are part of backend validation.

### Slurm backend

Submit the exact persisted, fully rendered JCF with `sbatch`; do not replace its body with an opaque `--wrap` command or silently override its requested allocation through command-line flags. Non-resource submission flags such as machine-readable response formatting are permitted and recorded. Retain the submitted file and digest. Observe live jobs with `squeue`, consult accounting with `sacct`, and request cancellation with `scancel`, retaining raw observations for diagnosis.

Use the same script body, input/output contract, and result-publication protocol as local execution, with explicit site-specific setup and scheduler configuration. Initialize environment modules for the selected shell; an interactive login environment cannot be assumed on compute nodes. Bash is the first reference renderer; Zsh and Fish require their own syntax/lifecycle tests and validated site support before being advertised as supported.

Normalize scheduler states without discarding the original state and reason. Combine scheduler observations with result manifests. Disappearance from the queue is not evidence of success. Support delayed or unavailable accounting with bounded reconciliation and an explicit unresolved outcome.

Batch polling, apply backoff, and limit submission rates. Keep workflow dependency decisions in the coordinator initially rather than maintaining a second dependency model in Slurm. Document supported Slurm versions and site capabilities once tested.

## 12. Observability and user interface

Provide durable execution events, diagnostic logs, and task stdout/stderr as distinct records. Include run ID, hierarchical node ID, attempt ID, and backend job ID where applicable.

Track queue time, execution duration, retry counts, exit status, scheduler reasons, requested resources, and available resource-usage observations. Explain waiting, skipped, closing, and uncertain states in user-facing output.

Proposed CLI commands, using a placeholder executable name:

```text
orchestrator validate workflow.py
orchestrator plan workflow.py
orchestrator plan workflow.py --format table
orchestrator plan workflow.py --tree
orchestrator render workflow.py --output preview/
orchestrator run workflow.py
orchestrator status RUN_ID
orchestrator inspect RUN_ID NODE_ID
orchestrator script RUN_ID NODE_ID --attempt 1
orchestrator logs RUN_ID NODE_ID
orchestrator cancel RUN_ID
orchestrator resume RUN_ID
orchestrator events RUN_ID --json
```

Use Click and Rich for commands and presentation. Provide stable machine-readable output independently of terminal formatting. Planning should expose the expanded graph, resolved resources, and control barriers without submission. CLI syntax remains provisional until the Python API and loading mechanism are defined.

The implemented `render` command previews scripts with preview identities/paths without submission; `script` verifies and prints the exact stored file for an existing attempt. Preview files are not runnable managed attempts. Inspection should also expose script/source digests, interpreter, submission arguments, and environment provenance. Documentation examples should lead with multi-command scripts, including local Bash, shell-specific Zsh/Fish examples once supported, and a self-describing Slurm JCF.

Implemented visualization (20 October 2026): `plan FILE --format table` displays a
Rich table for Python and YAML workflows. It includes every expanded node and direct
dependency in deterministic topological order, hierarchical IDs, scope enter/exit barriers,
task form/backend, generic CPU/timeout settings, explicit Slurm settings, scope concurrency
limits, branch guards, triggers, and required finalizers. This is a static view without
submission; row order does not impose serial execution. `plan FILE` retains its JSON
default, with `--format json` also supported. Terminal table documentation and coverage
for diamond dependencies, nesting, conditions, finalizers, YAML, literal formatting, and
unchanged JSON/no-submission behavior accompany the implementation.
Verification passed: 126 tests, Ruff lint/format checks, mypy, and the strict MkDocs build.

Tree visualization update (20 October 2026): `plan FILE --tree` (equivalently
`--format tree`) now renders prerequisites as Rich tree branches for Python and YAML.
Children are prerequisites of their parent. Scope barriers and redundant transitive
edges are collapsed while preserving dependency reachability; full hierarchical IDs,
decisions, branch guards, non-default triggers, and required finalizers remain visible.
Shared prerequisites expand once and are marked on later appearances, rather than
implying duplicate execution. Independent sinks form separate top-level branches.
JSON remains the default; the table and JSON retain every direct dependency/barrier.
Coverage includes diamond sharing, dependencies through nested scopes, YAML CLI behavior,
conflicting output options, control labels, and empty workflows. Documentation includes
the tree command and its direction/summary semantics.
Verification passed: 130 tests, Ruff lint/format checks, mypy, and the strict MkDocs build.

## 13. Package and engineering structure

Use a `src/` layout, `pyproject.toml`, `uv` with a committed lockfile, Ruff, one static type checker, pytest, and MkDocs. Choose and pin compatible tool versions during project initialization.

Suggested module boundaries:

```text
api/          Python builders and public types
spec/         Versioned models, serialization, validation
compiler/     Expansion, dependency resolution, source mapping
scripts/      Script-source capture, per-shell rendering, directive validation
engine/       State transitions, readiness, decisions, recovery
backends/     Local and Slurm adapters
storage/      State-store interface, schema, migrations
runner/       Task invocation and result publication
artifacts/    Artifact references and validation
cli/          Commands and presentation
```

Keep domain state transitions free from CLI, database, and Slurm dependencies. Use typed protocols or similarly small interfaces for external boundaries. Avoid a universal base class for every component.

Persisted specifications and database schemas require versioning and migration policy. Initially, reject unsupported versions with a clear error rather than silently interpreting them.

## 14. Implementation milestones and handoffs

Implement milestones in order. Other models should receive the relevant contract, owned modules, acceptance criteria, and non-goals before coding. Do not implement overlapping versions of the domain model or scheduler loop independently.

| Milestone | Deliverables | Acceptance criteria |
| --- | --- | --- |
| 0. Deployment and reuse decision | Architecture decisions for coordinator host, persistence, filesystem, site capabilities, and direct backend versus existing executor | A target deployment is documented; a small Parsl comparison establishes whether wrapping it would simplify execution without compromising required semantics. |
| 1. Specification and compiler | Public domain types, object-based I/O, script-source capture, Python builder, serialization, validation, nesting expansion | A nested serial/parallel example compiles deterministically; invalid references/cycles and conflicting script headers are rejected; script sources are captured without execution. |
| 2. State machine and local execution | Transition tables, readiness, runner, local backend, storage, reference Bash script renderer | A multi-command script stages inputs, executes, validates/collects outputs, and retains its exact script; failures block invalid descendants; attempts remain isolated; supported additional shells pass equivalent lifecycle tests. |
| 3. Durable coordination | Transactional events, script-bound action intents, ownership, retries, restart reconciliation | Restart preserves completed work and submitted script identity; changed source files do not alter retries; stale reports are harmless; uncertain actions are not blindly repeated. |
| 4. Control semantics | Branches, joins, bounded iterations, early completion, finalizers | A six-iteration example converges at four, never submits five or six, returns the correct output, and executes required finalization. |
| 5. Slurm integration | Site profiles, fully rendered JCFs, exact-file submission, polling, cancellation, accounting reconciliation | Real cluster smoke tests verify directives, environment setup, interpreter, and commands match the stored script; queue disappearance, accounting delay, rejection, and lost acknowledgements produce correct outcomes. |
| 6. Usability and release | CLI including script preview/inspection, Material for MkDocs documentation, script-first examples, DAG diagrams, provenance, packaging | A new user can inspect a self-describing script, run locally, configure a site, run on Slurm, interrupt/resume, and diagnose a failed task from its retained JCF, logs, and manifests. |

Each milestone should include its own documentation and meaningful tests; milestone 6 consolidates the user experience rather than postponing documentation until the end.

### Development tooling update (20 October 2026)

Milestone 6 now includes a GitHub Actions workflow for pushes, pull requests, and manual
runs. It uses Ubuntu 24.04, Python 3.12, uv 0.13.1, and locked development dependencies;
installs Bash/Zsh/Fish support; and runs Ruff lint, Ruff format checking, mypy, pytest,
and the strict MkDocs build. The operations guide now documents workflow schema v3
(SQLite schema remains v1) and retained script/source/metadata files in each script attempt.
The README and architecture status describe this CI coverage.

Local verification passed: 87 tests with Bash, Zsh, and Fish available, Ruff lint and
format checks, mypy, the strict documentation build, and workflow YAML parsing.

The workflow is configured; a successful hosted run remains to be verified after the
files are committed and pushed to GitHub. CI does not establish Slurm cluster compatibility.
Resource precedence, scheduler polling/backoff, separate storage roots, and Slurm
accounting/lost-ack reconciliation remain pending development work.

### Static YAML input update (20 October 2026)

YAML format v1 now supports reusable script/argument-list/Python task definitions,
flat dependency graphs, named workflow inputs and output references, object-based I/O
contracts, task/invocation resources, retries, concurrency limits, and required finalizers.
The `.yaml`/`.yml` CLI route and public `load_yaml` entry point construct the existing
Python builders and use the same compiler, schema v3, validation, and runtime. YAML is
an authoring frontend, not a separate execution engine or a new persisted schema.

Parsing uses PyYAML's safe loader with duplicate-key and alias rejection; unknown fields,
invalid types, and non-JSON values fail before execution. Literal artifact input paths and
script files resolve relative to the definition file. Documentation and a runnable script
example accompany coverage for Python/YAML specification equivalence, local execution,
CLI planning/rendering/resumption, path resolution and source capture, retry/finalizer
behavior, and malformed definitions. YAML includes, templating, nested workflows, decisions,
branch selection, and bounded iteration remain deferred; use the Python API for those controls.

Verification: the full local test suite, Ruff lint/format checks, mypy, the strict MkDocs
build, and offline lockfile consistency checks pass. PyYAML is now an explicit runtime
dependency, using its existing locked version rather than relying on the documentation tools.

## 15. Verification strategy

Focus testing on behavior and invariants:

- Compiler validation and deterministic nested expansion.
- Deterministic script rendering, source capture/digests, and byte-for-byte agreement between retained and submitted scripts.
- Directive placement, static value rendering, supported Slurm tokens, conflicting overrides, and inherited `SBATCH_*` settings.
- Shell-specific quoting, spaces/metacharacters in paths, interpreter selection, environment inheritance, module initialization, and activation.
- Multi-command and pipeline failure propagation for each supported shell, including explicit application error handling.
- Copy/link input staging, essential/optional files, validation before output collection, and visible lifecycle helper calls without duplicate staging/publication.
- Script-source edits after run creation, retries with distinct attempt paths, and recovery without rewriting submitted files.
- State-transition and readiness rules, including skipped dependencies.
- Equivalent policy decisions across different observation arrival orders when the required evidence is identical.
- Duplicate, stale, and out-of-order completion reports.
- Coordinator interruption before submission, after scheduler acceptance, and before result ingestion.
- Retry isolation and protection from old attempt results.
- Cancellation racing with successful completion.
- Required finalizer execution after early completion and failure.
- Scope limits across nested parallel workflows.
- Slurm parsing and normalization using recorded command fixtures.
- Real local subprocess integration and a documented real Slurm smoke suite.

Use a deterministic fake backend and controllable clock for failure injection. Test persistence with the actual initial database implementation. Mocked Slurm tests cannot establish site compatibility, so release qualification must include a real target cluster.

Critical invariants include: no task executes before its declared dependencies and control barriers permit it; only the current authorized attempt can determine task outputs; successful early stopping cannot enable later iterations; and unresolved submission uncertainty cannot silently create another attempt.

## 16. Decisions to settle before implementation

1. Where does the coordinator run, and what survives host failure or allocation expiry?
2. Which shared filesystem and publication guarantees are available?
3. What task scale and duration dominate: a few large simulations or many short operations?
4. Are MPI and GPU tasks required for the first usable release?
5. How is application code installed on compute nodes, and are package downloads prohibited there?
6. Is Slurm accounting available, and what version/site restrictions apply?
7. Is the initial product for one user or a shared service? This baseline assumes a per-user deployment.
8. Does an existing engine such as Parsl materially reduce execution complexity while preserving durable conditional semantics?
9. Which Bash/Zsh/Fish versions and module/activation systems are installed on coordinator and compute hosts?
10. Which static directives from existing Slurm scripts must be imported, and which site-specific options need a validated extension?
11. What environment inheritance policy and working/artifact directory conventions should the site use?

The accepted architecture does not depend on all future capabilities being implemented. Deployment and execution-contract answers do need to precede concrete backend and persistence choices.

## 17. Reference material

- [Parsl overview and execution model](https://parsl.readthedocs.io/en/stable/userguide/overview.html): Python dataflow orchestration and pilot-based execution as a reuse/comparison candidate.
- [Slurm sbatch](https://slurm.schedmd.com/sbatch.html): submission, resource requests, job metadata, and scheduler dependencies.
- [Slurm sacct](https://slurm.schedmd.com/sacct.html): job accounting and historical observations.
- [Slurm quick start](https://slurm.schedmd.com/quickstart.html): scheduler components and command roles.
- [Fish language documentation](https://fishshell.com/docs/current/language.html): shell-specific variable, pipeline, and execution semantics.
- [SQLite over a network](https://www.sqlite.org/useovernet.html): filesystem locking and synchronization considerations behind the local-database deployment baseline.

These references support the architectural review; implementation must verify behavior against the selected dependency versions and target cluster.


## 18. Requirements incorporated from working notes

- **Writing a JCF:** promoted into the primary task model and the rendering, execution, provenance, CLI, and verification requirements in sections 1–7 and 10–15. Most application tasks are command sequences; their retained scripts must describe Slurm requests, environment, and commands.
- **Setting/inheriting paths and building directories:** addressed by explicit environment/PATH inheritance, pre-submission directory creation, and working/artifact path contracts in sections 6–7. Site-specific path conventions remain an open deployment decision in section 16.
