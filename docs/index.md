# Orchestrator

Orchestrator builds workflow scripts without turning the workflow into a DAG.

You describe a workflow as JSON. The tool renders a directory of runnable shell
scripts. Each task script runs the task command, captures its exit status, and
then calls a mediation command. The mediator decides whether the workflow should
continue, stop, or ask the user what to do next.

The first implementation supports two output targets:

| Target | Behavior |
| --- | --- |
| `bash` | Runs task scripts locally with `bash`. |
| `slurm` | Adds `#SBATCH` directives to task scripts and submits command tasks with `sbatch --wait`. |

Workflows can be:

- `serial`: run immediate tasks one after another
- `parallel`: launch immediate tasks at the same time and wait for all of them

Tasks can also contain nested workflows, so a serial workflow can include a
parallel fan-out phase, or a parallel workflow can launch several serial
sub-workflows.

## Quick Start

Create `workflow.json`:

```json
{
  "name": "demo",
  "mode": "serial",
  "tasks": [
    {"name": "prepare", "command": "mkdir -p work && echo ready > work/input.txt"},
    {"name": "summarize", "command": "wc -c work/input.txt"}
  ]
}
```

Render scripts:

```bash
orchestrator build workflow.json --target bash --out-dir build/workflows
```

Run the workflow:

```bash
bash build/workflows/demo/run.sh
```

Build these docs locally:

```bash
uv sync --group docs
uv run mkdocs serve
```
