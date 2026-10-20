# Orchestrator

A Python-first workflow orchestrator with a working **Linux local-execution first pass**.
Define reusable tasks, compose nested workflows, inspect a deterministic plan, and execute
with isolated attempts and durable SQLite history.

Implemented: object-based input/output contracts with copy/link staging and validation,
Bash/Zsh/Fish script tasks, command and importable Python tasks, serial/parallel graphs, nested scopes,
branch selection and joins, bounded iteration with successful early stopping, required
finalizers, retries, concurrency limits, cancellation, and conservative restart recovery.
**Slurm submission is experimental and not cluster-qualified.** Script files retain their
resolved configuration, environment setup, commands, and provenance. This is an initial implementation of
[the project plan](plan.md), not a completed HPC release.

## Quick start

Requires Python 3.12+ and Linux. Run the bundled examples from this repository root:

```bash
uv sync
uv run orchestrator validate examples/diamond.py
uv run orchestrator plan examples/diamond.py
uv run orchestrator plan examples/diamond.py --format table
uv run orchestrator plan examples/diamond.py --tree
uv run orchestrator run examples/diamond.py --allow-local
```

For a static text definition, use YAML:

```bash
uv run orchestrator validate examples/script.yaml
uv run orchestrator run examples/script.yaml --allow-local
```

See [YAML definitions](docs/guide/yaml.md) for reusable tasks, dependencies, inputs, and
outputs. YAML and Python definitions compile through the same validation pipeline.
`plan --format table` displays a Rich table of steps, dependencies, control barriers,
and execution settings without submission; `plan` defaults to JSON.
`plan --tree` shows dependencies as branches, collapsing scope barriers and marking
shared prerequisites. Children are prerequisites of their parent; shared nodes run once.
`tasks` defines reusable work; `nodes` creates named steps using those definitions.
Each step has its own bindings and attempt history. `{$ref: write.report}` refers to
the `report` output of the step named `write`.

The diamond produces `{"value": 12}`. Local compute must be explicitly enabled because
the coordinator host might be an HPC login node. `--max-active 4` is the default global
concurrency limit. Local CPU counts are informational; runner timeouts are enforced.

More examples:

```bash
uv run orchestrator run examples/nested.py --inputs '{"start": 3}' --allow-local
uv run orchestrator run examples/branches.py --allow-local
uv run orchestrator run examples/convergence.py --allow-local
uv run orchestrator run examples/command.py --allow-local
uv run orchestrator run examples/staging.py --allow-local
uv run orchestrator run examples/script.py --allow-local
```

| Example | Composition | Expected result |
| --- | --- | --- |
| `diamond.py` | Seed → two parallel tasks → join | `12` |
| `nested.py` | Two concurrent child workflows, each with two serial tasks | `24` for start `3` |
| `branches.py` | Decision → one selected branch → optional-output join | `100` |
| `convergence.py` | Parent → bounded child loop → finalizer → parent report | Child stops at `4`; parent returns `8` |
| `command.py` | Argument-list command → validated file artifact | File containing `42` |
| `script.py` | Multi-command Bash task → retained script and report artifact | Greeting file |
| `script.yaml` | Static YAML task definition → named write step → workflow output | Greeting file |
| `staging.py` | Copy input → validate and collect output | Report artifact and line count |

## Script tasks

```python
from orchestrator import Script, Task

report = Task(script=Script(
    body="mkdir -p results\nprintf hello > results/report.txt",
    setup="export LC_ALL=C",
))
```

Use `Script(path="jobs/report.sh")` for an existing script.
See [script tasks and Slurm](docs/guide/scripts.md) for file capture, shell behavior,
environment configuration, scheduler headers, and preview/inspection commands.

## Small Python API example

Put an importable function in `my_tasks.py`:

```python
def double(value):
    return {"value": value * 2}
```

Export `workflow` from `workflow.py`:

```python
from orchestrator import Task, TaskInput, TaskOutput, Workflow

double = Task(
    python="my_tasks:double", inputs=[TaskInput("value")], outputs=[TaskOutput("value")]
)
workflow = Workflow("demo")
a = workflow.add("first", double, inputs={"value": 3})
b = workflow.add("second", double, inputs={"value": a.out("value")})
workflow.expose(value=b.out("value"))
```

Run `uv run orchestrator run workflow.py --allow-local`. The result is `12`.
Output bindings create dependencies automatically. Reusing `double` creates separate
invocations and attempts, not shared mutable task state.

## Inspect and recover

The CLI prints a run ID before starting. Replace `RUN_ID` below with that ID:

```bash
uv run orchestrator status RUN_ID
uv run orchestrator inspect RUN_ID diamond/left
uv run orchestrator script RUN_ID yaml_report/write
uv run orchestrator logs RUN_ID diamond/left
uv run orchestrator logs RUN_ID diamond/left --stderr --attempt 1
uv run orchestrator events RUN_ID --json
uv run orchestrator resume RUN_ID
uv run orchestrator cancel RUN_ID
```

Use node IDs belonging to that run: `diamond/left` is from the Python diamond example;
`yaml_report/write` is from the YAML script example. Retained script inspection applies
to script tasks.

Runs live in `.orchestrator/`. To change storage, put the group option **before** the command:
`uv run orchestrator --root /durable/local/runs run workflow.py --allow-local`.
Use the same root for inspection and resumption. Keep this directory on durable local
storage, not a network filesystem.

Ctrl-C stops the coordinator; detached task runners continue. `resume` reconciles those
attempts and preserves completed work. `cancel` writes a request; a running coordinator
handles it, or `resume` processes it later. Uncertain submissions are never automatically
repeated. See [operations and recovery](docs/guide/operations.md).

## Documentation and development

The documentation uses Material for MkDocs with light/dark modes, highlighted code and copy buttons.
The MkDocs site is organized into getting started, user guide, and reference sections.
Run `uv run mkdocs serve` for a local preview, or `uv run mkdocs build --strict` to build `site/`.

- [Documentation home](docs/index.md)
- [Quick start](docs/getting-started/quickstart.md)
- [Workflow DAGs and Graphviz sources](docs/guide/dags.md)

- [Workflow composition, with examples](docs/guide/workflows.md)
- [Static YAML definitions](docs/guide/yaml.md)
- [Script tasks and Slurm](docs/guide/scripts.md)
- [Task and artifact contracts](docs/guide/tasks.md)
- [CLI, run layout, and recovery](docs/guide/operations.md)
- [Architecture, state transitions, and implementation status](docs/reference/architecture.md)
- [Development checks and GitHub Actions](docs/reference/development.md)

```bash
uv run pytest
uv run ruff check src tests examples scripts
uv run ruff format --check src tests examples scripts
uv run mypy
uv run mkdocs build --strict
uv run mkdocs serve
```

Dependencies are resolved in `uv.lock`. `.python-version` selects Python 3.12;
`uv sync --python /path/to/python3.14` can use an already-installed supported interpreter.

GitHub Actions runs the five checks above (excluding `mkdocs serve`) on pushes and pull
requests, with manual runs also available. CI uses Ubuntu 24.04, Python 3.12, uv 0.13.1,
and `uv sync --locked --group dev`; Bash, Zsh, and Fish are installed so shell tests run.
CI checks local execution and mocked Slurm behavior; real cluster qualification is pending.
