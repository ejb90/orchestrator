# Philosophy

## Make application work inspectable

A script already expresses how to run an application. An orchestrator adds a different
layer: which scripts depend on which results, which resources they need, and what to do
when an execution fails or the coordinator restarts.

For the sensor report, `awk` knows how to filter measurements. Orchestrator knows that
reporting must wait for the filtered CSV, where that CSV should be staged, and which
attempt produced it. Keeping these responsibilities separate lets the same application
commands participate in different workflows.

The primary execution artifact is a retained shell script. It includes commands,
environment setup, file contracts, identity, and lifecycle helper calls. With Slurm,
it also contains the submitted scheduler directives. Inspecting a task should explain
what ran without reconstructing commands from a database.

## Separate policy from mechanism

**Policy** decides whether a step is eligible, which branch is selected, whether to retry,
and when a scope can finish. **Mechanism** launches a process, submits a scheduler job,
observes it, or requests cancellation.

The coordinator owns policy and orchestration state. Workers execute tasks and publish
results; backend adapters return observations. A worker does not start descendants or
mark database nodes successful on its own. This gives one place to explain decisions
even when processes finish in different orders.

## Prefer evidence to guesses

A process exiting, a scheduler job disappearing, and a validated result appearing are
different observations. None should silently stand in for another. A managed successful
task needs a matching result record and the required output checks.

When evidence is missing, the system can say `uncertain`. That is a useful outcome:
it prevents a restart from launching a second copy merely because an acknowledgement
was lost. The [recovery model](durability.md) explains this tradeoff.

## Make reproducibility explicit

Compilation records the specification, and script source is captured before execution.
Each attempt retains its script and identity. Directory bases are pinned when creating
the run, so editing a definition cannot silently move a retry or change its commands.

These records make execution explainable, but do not freeze the whole world. Application
packages, interpreters, sourced files, environment values, and input contents can still
change unless managed separately. A content digest detects changes to its recorded
artifact; it does not reproduce an entire computing environment.

## Keep the initial system small enough to reason about

The current deployment has one active coordinator per run and local SQLite ownership.
It prioritizes clear contracts and restart behavior over distributed coordination or
maximum throughput. This is a deliberate scope choice, not a claim that one host or
one filesystem suits every workload.

Use the [quick start](../getting-started/quickstart.md) for application work, or the
[architecture reference](../reference/architecture.md) for implementation boundaries
and capabilities still needing qualification.
