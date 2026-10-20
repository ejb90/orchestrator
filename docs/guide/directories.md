# Execution directories

`--root` chooses durable coordinator storage. A workflow, task definition, or step may
separately select a `directory` base for execution. Every task still runs in a fresh,
isolated attempt subdirectory; configuring a base never selects a shared exact cwd.

## Three anchors

| Configuration | Resolves against |
| --- | --- |
| Absolute, e.g. `/dev/shm/project` | That absolute filesystem location |
| `relative_to: root` | The individual run directory: `--root/RUN_ID` |
| `relative_to: parent` | The enclosing workflow's resolved base directory |

A plain string infers `absolute` for absolute paths and `parent` for relative paths.
`examples/directories.yaml` is a complete example with all three modes:

```bash
uv run orchestrator plan examples/directories.yaml --format table
uv run orchestrator run examples/directories.yaml --allow-local
```

The absolute example requires a writable `/dev/shm/orchestrator-example` on your host;
change that base if needed. Each output file records the actual isolated working directory.

For example, YAML task or node settings can use:

```yaml
directory: /dev/shm/project
```

```yaml
directory:
  path: scratch
  relative_to: root
```

```yaml
directory:
  path: preprocessing
  relative_to: parent
```

`root` always means the run directory, not the container directory supplied to `--root`.
`parent` refers to a logical workflow scope, never another task's active working directory.
Relative paths use normal filesystem resolution, including `..`.

## Python and nested workflows

```python
from orchestrator import Directory, Script, Task, Workflow

child = Workflow("preparation", directory=Directory("preprocessing", "parent"))
child.add("clean", Task(script=Script(body="pwd"), directory="clean"))
child.add("check", Task(script=Script(body="pwd")), directory=Directory("checks", "root"))

workflow = Workflow("analysis", directory="/dev/shm/project")
workflow.add("prepare", child)
```

The `clean` base resolves to `/dev/shm/project/preprocessing/clean`. The `check` base
resolves to `--root/RUN_ID/checks`, regardless of its parent's absolute base.
Nested workflows are available through Python; YAML v1 remains a flat graph.

A step's directory override takes precedence over its task or child-workflow definition.
Otherwise a task inherits its enclosing workflow's base. A workflow without a directory
inherits its parent's base; the root workflow defaults to the run directory. Unconfigured
workflows keep the existing default attempt layout.

## Isolated paths and durable metadata

For bases inside the run directory, attempts live at:

```text
BASE/attempts/WORKFLOW/NODE/ATTEMPT_ID/
```

For external bases, a run namespace prevents different runs from sharing attempt paths:

```text
BASE/RUN_ID/attempts/WORKFLOW/NODE/ATTEMPT_ID/
```

SQLite, ownership, cancellation requests, and the stored specification stay under
`--root/RUN_ID`. Inputs, retained scripts, logs, and results live in the selected attempt
directory. `inspect`, `script`, and `logs` follow the stored paths automatically.
`plan --format table` shows explicit directory settings without creating those directories;
`render --output` still writes previews only to its requested preview location.

Paths such as `/dev/shm` are host-local and volatile. Use them for local execution when
that is appropriate; Slurm attempts require storage accessible to both the coordinator
and compute nodes at the same absolute paths. Directory settings do not transport files
between disconnected filesystems, provision dependencies, or change Slurm placement.

??? info "Theory: Filesystem namespaces are not scheduling dependencies"

    A directory base identifies where an attempt should store its private files. A
    parent-relative directory follows the enclosing workflow configuration, not another
    task's live cwd. Unique run/node/attempt paths preserve identity across task reuse
    and retries; choosing a path does not create a dependency or allocate a worker.

    Read more: [Execution namespaces](../fundamentals/contracts.md#directories-are-namespaces-not-dependency-edges).

## Retries, resume, and control decisions

All task bases are resolved and persisted when creating the run. Each new attempt gets
a unique path and retained script from the captured task definition. A retry cannot
reuse an earlier attempt directory, and later edits to the authoring file do not change
the base. Reusing one task in several steps remains isolated by node and attempt IDs.

Submission intent records the exact attempt path before launching work. Resume observes
that same path and job identity; it does not relocate an active job or regenerate its
submitted script. A lost acknowledgement is reconciled conservatively. Missing scratch
directories or result records can produce `uncertain`, which never authorizes an automatic
resubmission. The implementation has no force-resubmit command.

Branch guards, bounded iteration, early stopping, cancellation, and required finalizers
still govern eligibility. Directory resolution does not execute or create directories for
skipped/unstarted tasks; finalizers use their own pre-resolved bases and isolated attempts.
Arbitrary runtime graph expansion and directory interpolation remain unsupported.

??? info "Theory: Durable metadata cannot restore volatile data"

    SQLite can remember which attempt ran on scratch storage without being able to
    recover deleted files from that storage. A missing result is not proof that no work
    ran. Keeping the same path and an uncertain outcome avoids creating a second attempt
    merely to replace missing evidence.

    Read more: [Durability and uncertain execution](../fundamentals/durability.md#transactions-have-a-boundary).

New runs use compiled workflow schema v4. Existing v3 runs without directory configuration
remain resumable with their original layout. YAML authoring stays at version 1 and SQLite
at schema version 1; earlier workflow schema versions remain unsupported.
