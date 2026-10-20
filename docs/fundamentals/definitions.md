# Definitions and compilation

## A definition is not an execution

Think of a reusable task as a function definition: it describes work that can be used
more than once. A named workflow step is a particular use of that definition with
bindings and overrides. Executing the workflow creates runtime records rather than
changing the reusable task into a running process.

| Concept | Meaning | Sensor-report example |
| --- | --- | --- |
| Task definition | Reusable script/operation, contracts, and defaults | `clean_readings` |
| Step / invocation | Named placement with inputs, dependencies, and overrides | `clean` |
| Workflow definition | Composition and public input/output bindings | `sensor_report` |
| Run | One execution of a stored specification with particular inputs/configuration | One run ID |
| Node run | Runtime state of one step in that run | Whether `sensor_report/clean` succeeded |
| Attempt | One isolated execution try for that step | The first cleaner try, or a retry |
| Backend job | The process or scheduler job carrying out an attempt | Local runner PID or Slurm job ID |

Two steps can reuse one task with different input files and resource overrides. They
still have separate outputs and attempt histories. A retry adds an attempt to the same
step; it does not add a new logical workflow step.

## Author, compile, render, execute

The stages have distinct jobs:

```text
Python or YAML → builders → compiled specification → coordinator → task attempts
                                 │                                  │
                             validation                       retained job scripts
```

Python and YAML are authoring frontends. Both build the same domain objects and use the
same compiler. The compiler resolves bindings, expands nested workflows, captures script
source, and rejects invalid graphs or contracts. It does not run application commands.
Loading a Python definition still executes that file's top-level Python, so its author
must keep definition-time side effects under control.

An attempt's final script is rendered later, when its identity, paths, and resolved
inputs are known. A preview can show commands before submission, but has illustrative
identities rather than authorization to publish a real attempt result.

## Binding connects names to data

The task contract names ports such as `readings` and `summary`. A step binds its input
ports to literal values, workflow inputs, or another step's output.

`{$ref: clean.readings}` identifies the `readings` output of the step `clean`. It also
creates a dependency. A top-level output binding exposes a result to the caller; it
does not execute another task or copy a file again.

Nested workflows expose public inputs and outputs in the same way. Callers use this
interface rather than reaching into internal child steps. The compiler retains
hierarchical IDs so repeated child workflows remain distinct.

## Versions and hashes describe different things

An authoring-format version describes the YAML language. A compiled-specification
version describes the executable graph representation. The database schema version
describes storage. They need not advance together.

The specification digest identifies its recorded content. It helps detect a changed
stored specification; it is not a substitute for version compatibility or input-file
content validation. Current supported versions are listed in
[operations](../guide/operations.md#specification-compatibility).

Continue with [graphs](graphs.md), or see the [practical walkthrough](../getting-started/practical-workflows.md)
for the concrete YAML and Python bindings.
