# Quick start: turn sensor readings into a report

This workflow copies a CSV into an isolated working directory, removes rejected readings,
and passes the cleaned file to a second task that writes a temperature report. Everything
runs locally using Bash, `awk`, and standard Linux commands; no cluster or application
packages are needed.

## Install and inspect

Requires Python 3.12+ and Linux. Run these commands from the repository root:

```bash
uv sync --locked --group dev
uv run orchestrator validate examples/sensor_report.yaml
uv run orchestrator plan examples/sensor_report.yaml --tree
```

```text
sensor_report — prerequisites
└── sensor_report/report (task)
    └── sensor_report/clean (task)
```

The `report` step consumes the `clean` step's output. Use `--format table` instead of
`--tree` for execution settings and scope barriers, or omit output options for JSON.

The input is `examples/data/sensor_readings.csv`: seven readings, including one marked
`bad` and one marked `missing`. The shell scripts live in `examples/jobs/`. The YAML
file describes how to stage files and connect the two steps.

??? info "Theory: Why two steps and an output reference?"

    The cleaner and reporter have separate execution and output contracts. The
    reference supplies a file and declares a prerequisite, rather than relying on the
    order of entries in the definition. Each step receives its own staged inputs and
    attempt history.

    Read more: [The fundamental model](../fundamentals/index.md).

## Run and read the report

```bash
uv run orchestrator run examples/sensor_report.yaml --allow-local
```

`--allow-local` explicitly permits compute on this host. The command prints a run ID,
the step states, and absolute paths to the `clean_csv` and `report` output artifacts.
Each step gets its own script, input copy, logs, and result manifest under
`.orchestrator/RUN_ID/`. The original CSV is left unchanged.

Replace `RUN_ID` with the printed identifier:

```bash
uv run orchestrator logs RUN_ID sensor_report/report
```

```text
Temperature report
Accepted readings: 5
Mean temperature: 20.6 C
Range: 18.5 to 23.0 C
```

The same text is saved at `report.path` in the run's outputs. The cleaned CSV contains
its header and five accepted rows. The rejected 99°C reading and the missing reading
are excluded before calculating the statistics.

## Inspect what ran

```bash
uv run orchestrator status RUN_ID
uv run orchestrator inspect RUN_ID sensor_report/clean
uv run orchestrator script RUN_ID sensor_report/report
uv run orchestrator events RUN_ID --json
uv run orchestrator resume RUN_ID
```

`script` verifies and prints the exact retained Bash job file. Sources are captured
during compilation, so later edits do not change a retry or resumed run. Resuming this
completed workflow preserves its results without executing the steps again.

To preview scripts before running, choose a new output directory:

```bash
uv run orchestrator render examples/sensor_report.yaml --output sensor-preview/
```

Preview files are illustrative rather than managed executable attempts. Use `--help`
or `-h` for colourful command help.

## Use Python instead of YAML

The equivalent Python workflow reads the same dataset and runs the same shell scripts:

```bash
uv run orchestrator plan examples/sensor_report.py --tree
uv run orchestrator run examples/sensor_report.py --allow-local
```

Choose YAML for static definitions or Python for the full composition API. The
[practical walkthrough](practical-workflows.md) explains both definitions and their bindings.

## Summarize an application log

A second concrete example counts messages in `examples/data/application.log`:

```bash
uv run orchestrator run examples/log_summary.yaml --allow-local
uv run orchestrator logs RUN_ID log_summary/summarize
```

Use the new run ID. The report contains six entries, one warning, and two errors.
These are messages found in the input log; the summarization task itself succeeds.

## Choose storage and go further

Global `--root PATH`, placed before the command, chooses durable coordinator storage.
Keep using that root for inspection. Task/workflow `directory` settings separately
choose execution bases relative to the run root, relative to their parent workflow,
or at absolute paths such as `/dev/shm/project`. All retain isolated attempts; see
[execution directories](../guide/directories.md) for examples and recovery behavior.

- [Fundamentals](../fundamentals/index.md): philosophy, graphs, contracts, and recovery.
- [Practical workflows](practical-workflows.md): source files, task definitions, and output bindings.
- [YAML definitions](../guide/yaml.md): supported fields and validation.
- [Workflow composition](../guide/workflows.md): small examples isolating parallelism, nesting,
  branching, iteration, retries, and finalizers.
- [Workflow DAGs](../guide/dags.md): diagrams and terminal views of the composition examples.
- [Operations](../guide/operations.md): concurrency, cancellation, and restart behavior.
- [Development and CI](../reference/development.md): docs preview, checks, and API reference.
