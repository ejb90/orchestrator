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

Requires Python 3.12+ and Linux, with Bash, awk, and standard shell utilities. From the repository root:

```bash
uv sync --locked --group dev
uv run orchestrator validate examples/sensor_report.yaml
uv run orchestrator plan examples/sensor_report.yaml --tree
uv run orchestrator run examples/sensor_report.yaml --allow-local
```

The workflow cleans sensor measurements and writes a temperature report. It rejects the
bad and missing readings, retains five measurements, and reports a mean of 20.6 C and
a range of 18.5–23.0 C. The outputs include both the cleaned CSV and the report file.
Replace `RUN_ID` below with the identifier printed by `run`:

```bash
uv run orchestrator logs RUN_ID sensor_report/report
```

The equivalent Python definition runs the same scripts:

```bash
uv run orchestrator run examples/sensor_report.py --allow-local
```

A second practical workflow summarizes application log messages:

```bash
uv run orchestrator run examples/log_summary.yaml --allow-local
```

It writes a report containing six entries, one warning, and two errors. Logged errors
are input data; the summarization task succeeds. See the [quick start](docs/getting-started/quickstart.md)
and [practical walkthrough](docs/getting-started/practical-workflows.md) for the source files,
contracts, expected reports, and inspection commands.

`plan --tree` shows prerequisites as branches; `--format table` shows all dependencies
and execution settings. The default plan output is JSON. Local compute requires
`--allow-local`; `--max-active` defaults to four. CPU counts are informational locally.

The detailed [workflow guide](docs/guide/workflows.md) and [DAG gallery](docs/guide/dags.md)
use the smaller `diamond`, `nested`, `branches`, and `convergence` examples to isolate
composition and control semantics. The greeting, command, and staging examples also
remain available for the corresponding detailed guides.

## Script tasks

The practical examples use retained Bash scripts with explicit file contracts. Each
attempt captures the source and retains the executed script, staging details, logs, and
validated results. See [script tasks](docs/guide/scripts.md) for inline/file authoring,
Bash/Zsh/Fish behavior, environments, and experimental Slurm submission.

## Python and YAML definitions

The sensor workflow is available as `examples/sensor_report.py` and `examples/sensor_report.yaml`.
Both compile to the same specification. Reusable tasks define commands and file contracts;
named steps connect them with input/output bindings. See the
[practical walkthrough](docs/getting-started/practical-workflows.md) for both versions and
the [YAML reference](docs/guide/yaml.md) for supported fields.

## Inspect and recover

The CLI uses Rich Click for colourful, grouped help. Try `uv run orchestrator --help`
or `uv run orchestrator run -h`. Run states, dependency tables, and trees are styled;
`NO_COLOR=1` disables colour. JSON, logs, and retained scripts remain plain output.

The CLI prints a run ID before starting. Replace `RUN_ID` below with that ID:

```bash
uv run orchestrator status RUN_ID
uv run orchestrator inspect RUN_ID sensor_report/clean
uv run orchestrator script RUN_ID sensor_report/report
uv run orchestrator logs RUN_ID sensor_report/clean
uv run orchestrator logs RUN_ID sensor_report/clean --stderr --attempt 1
uv run orchestrator events RUN_ID --json
uv run orchestrator resume RUN_ID
uv run orchestrator cancel RUN_ID
```

These node IDs belong to either sensor-report definition. Script inspection verifies
and prints the retained job file for the selected step and attempt.

Runs live in `.orchestrator/`. To change storage, put the group option **before** the command:
`uv run orchestrator --root /durable/local/runs run examples/sensor_report.yaml --allow-local`.
Use the same root for inspection and resumption. Keep this directory on durable local
storage, not a network filesystem. Task/workflow `directory` settings separately select
isolated execution bases using absolute, run-root-relative, or parent-relative paths;
see [execution directories](docs/guide/directories.md).

Ctrl-C stops the coordinator; detached task runners continue. `resume` reconciles those
attempts and preserves completed work. `cancel` writes a request; a running coordinator
handles it, or `resume` processes it later. Uncertain submissions are never automatically
repeated. See [operations and recovery](docs/guide/operations.md).

## Documentation and development

The documentation uses Material for MkDocs with light/dark modes, highlighted code and copy buttons.
The MkDocs site separates getting started, fundamentals, user guides, and reference.
Fundamentals explains the philosophy and computer science; contextual Theory boxes in
the guides are collapsed by default to keep practical instructions easy to follow.
Run `uv run mkdocs serve` for a local preview, or `uv run mkdocs build --strict` to build `site/`.

- [Documentation home](docs/index.md)
- [Quick start](docs/getting-started/quickstart.md)
- [Fundamentals: philosophy and computer science](docs/fundamentals/index.md)
- [Workflow DAGs and Graphviz sources](docs/guide/dags.md)

- [Workflow composition, with examples](docs/guide/workflows.md)
- [Static YAML definitions](docs/guide/yaml.md)
- [Script tasks and Slurm](docs/guide/scripts.md)
- [Task and artifact contracts](docs/guide/tasks.md)
- [CLI, run layout, and recovery](docs/guide/operations.md)
- [Architecture, state transitions, and implementation status](docs/reference/architecture.md)
- [Generated API reference](docs/reference/api/index.md)
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

The API reference uses mkdocstrings' Python handler to render classes, functions,
methods, signatures, annotations, docstrings, and source from `src/` during each build.
It covers all implementation modules, including private helpers; constructors are merged
into their class entries. See [documentation development](docs/reference/development.md#api-documentation)
for adding modules and writing docstrings.
