# Quick start

Orchestrator requires Python 3.12+ and Linux. The examples below execute locally.
For experimental Slurm submission, see [script tasks](../guide/scripts.md). Run these commands from the repository root:

```bash
uv sync
uv run orchestrator validate examples/diamond.py
uv run orchestrator plan examples/diamond.py
uv run orchestrator plan examples/diamond.py --format table
uv run orchestrator plan examples/diamond.py --tree
uv run orchestrator run examples/diamond.py --allow-local
```

The diamond workflow returns `{"value": 12}`. `--allow-local` explicitly permits compute
on the coordinator host; `--max-active` defaults to four concurrent tasks.

## Write a workflow

Choose YAML for a flat static graph, or Python for the full composition API, including
nested workflows, branches, and bounded iteration. Both use the same execution engine.

### YAML

Run the bundled script example:

```bash
uv run orchestrator validate examples/script.yaml
uv run orchestrator plan examples/script.yaml
uv run orchestrator plan examples/script.yaml --format table
uv run orchestrator render examples/script.yaml --output yaml-preview/
uv run orchestrator run examples/script.yaml --allow-local
```

It creates a greeting report and exposes its artifact reference as the workflow's
`report` output. `render` creates a new preview directory without running the task.
See [YAML definitions](../guide/yaml.md) to write your own `.yaml` or `.yml` file and
understand `tasks`, `nodes`, and `{$ref: step.output}` bindings.

### Python

Create an importable `my_tasks.py` alongside your workflow:

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

```bash
uv run orchestrator run workflow.py --allow-local
```

The result is `12`. Output references automatically add dependencies. Reusing a task
creates separate invocations and isolated attempts.

## Try other compositions

```bash
uv run orchestrator run examples/nested.py --inputs '{"start": 3}' --allow-local
uv run orchestrator run examples/branches.py --allow-local
uv run orchestrator run examples/convergence.py --allow-local
uv run orchestrator run examples/command.py --allow-local
uv run orchestrator run examples/staging.py --allow-local
uv run orchestrator run examples/script.py --allow-local
```

The [DAG gallery](../guide/dags.md) shows each example, including downloadable Graphviz
sources. The [workflow guide](../guide/workflows.md) explains how to combine these patterns.

## Inspect a run

Replace `RUN_ID` with the identifier printed by `run`:

```bash
uv run orchestrator status RUN_ID
uv run orchestrator logs RUN_ID diamond/left
uv run orchestrator events RUN_ID --json
uv run orchestrator resume RUN_ID
uv run orchestrator cancel RUN_ID
```

For the YAML script example, use the node ID `yaml_report/write` with `inspect`, `logs`,
and `script`:

```bash
uv run orchestrator inspect RUN_ID yaml_report/write
uv run orchestrator script RUN_ID yaml_report/write
uv run orchestrator logs RUN_ID yaml_report/write
```

`script` verifies and prints the exact retained job file for that attempt. Script
sources are captured during compilation, so later source edits do not change a retry.

By default, runs are stored in `.orchestrator/`. Put `--root PATH` before the subcommand
to choose another durable local directory. See [operations and recovery](../guide/operations.md)
for cancellation, restart semantics, and uncertainty handling.

## Build and browse the documentation

```bash
uv run mkdocs serve
uv run mkdocs build --strict
```

`serve` starts the local documentation preview; `build` writes a static site to `site/`.
The site uses Material for MkDocs, with light/dark themes, syntax-highlighted code,
copy buttons, search, and section navigation. Diagram assets are committed, so no diagram
plugin or Graphviz installation is required to build it.
See [development and CI](../reference/development.md) for the automated checks.
