# Orchestrator

Build workflow scripts without building a DAG.

`orchestrator` takes a declarative workflow and renders runnable bash scripts, or
Slurm job command files. Each task script runs its command and then calls a
mediation command. The default mediator records task completion and decides what
happens after failure:

- successful tasks continue normally
- failed tasks ask whether to continue when attached to a TTY
- failed non-interactive tasks stop the workflow by writing a shared stop file

Workflows can run their immediate tasks in `serial` mode or `parallel` mode.
Tasks can also be sub-workflows.

## Install

The project targets Python 3.12.

```bash
uv sync
```

Build or preview the documentation with MkDocs:

```bash
uv sync --group docs
uv run mkdocs serve
```

## Workflow file

```json
{
  "name": "demo",
  "mode": "serial",
  "tasks": [
    {
      "name": "prepare",
      "command": "mkdir -p work && echo ready > work/input.txt"
    },
    {
      "name": "fanout",
      "workflow": {
        "name": "parallel-work",
        "mode": "parallel",
        "tasks": [
          {"name": "alpha", "command": "wc -c work/input.txt"},
          {"name": "beta", "command": "sha256sum work/input.txt"}
        ]
      }
    },
    {
      "name": "summarize",
      "command": "echo done"
    }
  ]
}
```

Render bash scripts:

```bash
orchestrator build workflow.json --target bash --out-dir build/workflows
```

Run the generated workflow:

```bash
bash build/workflows/demo/run.sh
```

Render Slurm-oriented task scripts:

```bash
orchestrator build workflow.json --target slurm --out-dir build/slurm
```

Task-level Slurm directives can be attached with `slurm`:

```json
{
  "name": "compute",
  "command": "python run.py",
  "slurm": {
    "time": "00:30:00",
    "cpus-per-task": "4",
    "mem": "8G"
  }
}
```

## Mediation

Every generated task ends with a command like:

```bash
orchestrator mediate --workflow demo --task prepare --status "$status" --state-dir "$STATE_DIR"
```

The default mediator appends JSON lines to `events.jsonl` and writes `stop` when
the workflow should halt. You can swap in your own mediation command:

```bash
orchestrator build workflow.json --mediator "python path/to/mediate.py"
```

The custom mediator should accept the same `--workflow`, `--task`, `--status`,
and `--state-dir` arguments and return `0` to continue.
