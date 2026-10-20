# Task inputs, outputs, and artifacts

Tasks declare **lists of `TaskInput` and `TaskOutput` objects**. Each object names a port
and describes its data contract. Artifact ports additionally describe staging or collection.
Workflow branching and early stopping remain explicit decision policies.

The Python API uses the objects below; [YAML definitions](yaml.md) express the same
contracts as lists of mappings under each reusable task. Workflow steps select a task
definition and supply bindings and overrides; separate steps have separate attempts.

```python
from orchestrator import Task, TaskInput, TaskOutput

simulation = Task(
    python="my_tasks:simulate",
    inputs=[
        TaskInput(
            "config",
            kind="file",
            source="settings.json",
            destination="work/settings.json",
            mode="copy",
            essential=True,
        ),
        TaskInput("checkpoint", kind="file", essential=False),
        TaskInput("steps", source=100),
    ],
    outputs=[
        TaskOutput(
            "results",
            kind="file",
            source="results.csv",
            destination="published/results.csv",
            essential=True,
            validator="my_tasks:valid_results",
        ),
        TaskOutput(
            "diagnostics", kind="directory", source="diagnostics", essential=False
        ),
        TaskOutput("score"),
    ],
)
```

!!! note "Names and bindings are separate"
    `TaskInput` and `TaskOutput` describe the reusable task contract. An invocation can
    override input sources through `workflow.add(..., inputs={...})`. `Input("name")`
    still refers to a **workflow's** named input; it is distinct from `TaskInput`.

## Input objects

| Field | Default | Meaning |
| --- | --- | --- |
| `name` | Required | Unique port name; passed as a Python keyword argument |
| `kind` | `"value"` | `value`, `file`, or `directory` |
| `source` | `None` | Default value, path, workflow input, or upstream output reference; `None` means unbound |
| `destination` | `None` | Attempt-relative staging path; artifacts default to `inputs/NAME` |
| `mode` | `"copy"` | Copy the artifact or create a symbolic `link` |
| `essential` | `True` | Whether the input must be supplied and the artifact must exist |
| `default` | `None` | Fallback for an unbound optional value input; optional artifact inputs use `None` |

Invocation bindings take precedence over `source`. A required unbound input is a compile
error. `source=None` is the unbound marker; pass an explicit null value through the invocation
if needed. Values are finite JSON-compatible data. Relative literal artifact sources are
resolved to absolute paths against the **compilation working directory** in Python,
then persisted. Use an absolute path or construct a path from `__file__` for a relocatable
Python definition file. Literal artifact sources authored in YAML instead resolve against
the YAML file's directory; paths supplied through `--inputs` retain working-directory behavior.

```python
from orchestrator import Task, TaskInput, TaskOutput, Workflow

number = Task(
    python="examples.tasks:number",
    inputs=[TaskInput("value", source=3)],
    outputs=[TaskOutput("value")],
)
workflow = Workflow("bindings")
a = workflow.add("first", number)  # Uses source=3.
b = workflow.add("second", number, inputs={"value": 7})  # Overrides source.
workflow.expose(first=a.out("value"), second=b.out("value"))
```

An input source can also be `producer.out("data")`, `Input("workflow_parameter")`, or
`Select(...)`. References create dependencies whether supplied on the port or invocation.
For an optional producer that may fail or be skipped, use `Select` and an appropriate
trigger; `essential=False` alone does not relax graph dependencies.

### Copying and linking

Artifact inputs are staged **before task code starts**, inside the isolated attempt directory.
Python callables receive `{"path": "/absolute/staged/path", "kind": "file"}` (or
`directory`); optional missing artifact inputs receive `None`. Commands read the same values
from their staged input manifest.

Copy mode gives the task its own file or directory. Link mode creates a symbolic link
and retains access to the original data. Directory copies preserve symlinks inside the tree.

!!! warning "Links share the original data"
    Writing through a linked input changes its source. Choose `copy` when the task will
    modify inputs. Copies of directory trees preserve existing internal symlinks, so their
    targets are still shared.

Missing essential artifacts fail the attempt without running task code. Missing optional
artifacts become `None`. An existing artifact of the wrong kind still fails, even when
optional. Input destinations cannot be absolute, escape through `..` or a parent symlink,
use runner-owned paths, or overlap another transfer destination.

## Output objects

| Field | Default | Meaning |
| --- | --- | --- |
| `name` | Required | Unique output name, exposed through `handle.out(name)` |
| `kind` | `"value"` | `value`, `file`, or `directory` |
| `source` | `None` | Artifact path relative to the attempt; when omitted, task code returns its path by name |
| `destination` | `None` | Collection path; relative paths stay in the attempt, absolute paths explicitly export |
| `mode` | `"copy"` | Copy or symbolically link when a collection destination is specified |
| `essential` | `True` | Missing required output fails; missing optional output is omitted |
| `validator` | `None` | Importable `module:function` that must return `True` to accept the output |

Value outputs are returned by name; they do not have filesystem sources, destinations,
or transfer modes. Artifacts can either declare a static source or return a dynamic path,
but cannot do both for the same output.

When `destination` is omitted, the artifact is validated and referenced in place. When a
destination is specified, it is collected there after all outputs pass validation. The
published artifact reference points at the collected file or directory. Link mode publishes
a symbolic link, so its source must remain available.

Relative destinations retain retry isolation. For example, each attempt gets its own
`published/results.csv`. Absolute destinations deliberately export outside the attempt;
choose unique paths per run/task to avoid collisions.

!!! warning "Collection never overwrites existing data"
    A destination that already exists, including a dangling symlink, fails the attempt.
    This also applies when a destination equals its source. A failed or interrupted copy
    may leave partial data, and collecting multiple outputs is not a transaction. Earlier
    absolute exports can remain if a later collection fails, potentially blocking a retry.
    Prefer attempt-relative collection and downstream publishing tasks for repeatable workflows.

Missing optional outputs are absent from the result dictionary. Consume them with
`Select((task.out("optional"),), default=...)`; a direct reference still requires the output
at runtime. Explicit `None` is valid for a value output; for an artifact it means absent.

### Validation logic

Keep output logic local to the output contract: requiredness, existence/type checks,
and application validation. Validators receive the small value or artifact-reference
object, run in the worker after execution, and return exactly `True` on acceptance:

```python
from pathlib import Path


def valid_results(artifact):
    path = Path(artifact["path"])
    return path.stat().st_size > 0 and path.read_text().startswith("iteration,score")
```

A false return or exception fails the attempt before collection begins. Validators should
be deterministic and avoid side effects. They do not select workflow branches or submit
work. Only the worker runs the validator; coordinator reconciliation checks result identity,
required output names, JSON serialization, and artifact existence without rerunning user
code or repeating collection. No checksums or content immutability are guaranteed.

## Implementing a Python task

Task references use `package.module:function`, optionally with attribute components.
Closures and captured live objects are not supported. Code and dependencies must be
installed/importable in the worker's environment.

For the simulation contract above:

```python
from pathlib import Path


def simulate(config, checkpoint, steps):
    settings = Path(config["path"]).read_text()
    # Run the application using settings, optional checkpoint, and steps.
    Path("results.csv").write_text("iteration,score\n1,0.5\n")
    return {"score": 0.5}
```

The runner collects `results.csv` from the declared source; the callable need not return
its path. It may return `None` when every output is collected from a declared artifact
source, or when it has no outputs. Otherwise return a dictionary containing the value
outputs and any artifact paths without declared sources. Unknown names are rejected.

To choose a path dynamically:

```python
from pathlib import Path

from orchestrator import Task, TaskOutput


def write_report():
    Path("report.txt").write_text("done\n")
    return {"report": "report.txt"}


report_task = Task(
    python="my_tasks:write_report",
    outputs=[TaskOutput("report", kind="file")],
)
```

## Commands

Commands are argument lists, never implicit shell strings. They receive:

- `ORCHESTRATOR_INPUTS`: path to `staged-inputs.json`, containing attempt identity,
  task definition, and staged `inputs`.
- `ORCHESTRATOR_OUTPUTS`: path where a command can write its output dictionary.

The original `input.json` retains unstaged resolved inputs. A command must exit zero.
It need not write an output dictionary when all essential outputs have declared artifact
sources. Otherwise it writes the remaining outputs to `ORCHESTRATOR_OUTPUTS`. It must not
write runner-owned `result.json`.

```python
from orchestrator import Task, TaskInput, TaskOutput

command_task = Task(
    command=("/usr/bin/tool", "work/settings.json", "results.csv"),
    inputs=[
        TaskInput(
            "config",
            kind="file",
            source="settings.json",
            destination="work/settings.json",
        )
    ],
    outputs=[TaskOutput("results", kind="file", source="results.csv")],
)
```

Use `Task(command=("/bin/bash", "-lc", "..."))` for an explicit shell. Read dynamic inputs
from the staged manifest rather than interpolating them into shell text. There is no
command-argument templating. See `examples/command.py` for dynamic output manifests and
`examples/staging.py` for the object-based collection contract.

## Attempts, environments, and resources

Every attempt gets an isolated working directory, original/staged manifests, stdout/stderr,
runner identity, and terminal result. Custom working directories are deferred. `Task(env=...)`
extends the inherited environment; Python callables use the coordinator's Python executable.
Script tasks use their declared shell and support an explicit environment inheritance policy.
Resolved definitions and sources are persisted, so keep secrets out of them.

Resource defaults resolve from outer workflow → inner workflow → task definition → task
invocation override. Each resource object replaces the inherited object. A workflow invocation
override acts as an inherited default for its children; child defaults take precedence.

`Resources(cpus=1, timeout_seconds=None)` is the supported model. Local CPU counts are informational;
positive timeouts use a runner timer and process-group termination.
See [script tasks](scripts.md) for Bash/Zsh/Fish interpreters, retained job files, and
experimental Slurm placement. Containers and environment provisioning remain deferred.
Tasks must not daemonize or defeat runner signal handlers. Unknown process completion is
reported as uncertainty rather than falsely confirming success.

Manifest publication writes a temporary file, flushes/fsyncs it, then renames it in the
same directory. General manifest directory fsync and host-power-loss guarantees remain
incomplete; retained script files and their directory are fsynced before submission intent.

## Migration from the initial API

Replace string tuples and output-kind dictionaries with object lists:

```python
from orchestrator import Task, TaskInput, TaskOutput

# Previously: inputs=("value",), outputs={"value": "value"}
task = Task(
    python="examples.tasks:double",
    inputs=[TaskInput("value")],
    outputs=[TaskOutput("value")],
)
```

Workflow input names and invocation binding dictionaries are unchanged. The compiled
workflow schema is now **version 3**, including captured script definitions. Version 1
and version 2 persisted runs are rejected on resume. The SQLite storage schema remains v1;
there is no automatic migration. Preserve old runs and use the matching previous code version to
finish them, or start a deliberate new run with the updated definitions.
