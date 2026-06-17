# Examples

These examples are complete workflow JSON files.

## Serial Workflow

Use serial mode when each step depends on the previous step's side effects.

```json
{
  "name": "serial-demo",
  "mode": "serial",
  "tasks": [
    {
      "name": "prepare",
      "command": "mkdir -p work && printf 'alpha\\nbeta\\n' > work/input.txt"
    },
    {
      "name": "count",
      "command": "wc -l work/input.txt"
    },
    {
      "name": "archive",
      "command": "tar -czf work/results.tgz work/input.txt"
    }
  ]
}
```

Build and run:

```bash
orchestrator build serial-demo.json --out-dir build/workflows
bash build/workflows/serial-demo/run.sh
```

## Parallel Workflow

Use parallel mode when immediate tasks can be launched together.

```json
{
  "name": "parallel-demo",
  "mode": "parallel",
  "tasks": [
    {"name": "python version", "command": "python --version"},
    {"name": "disk usage", "command": "du -sh ."},
    {"name": "list files", "command": "find . -maxdepth 2 -type f | sort"}
  ]
}
```

Build and run:

```bash
orchestrator build parallel-demo.json --out-dir build/workflows
bash build/workflows/parallel-demo/run.sh
```

## Serial Workflow with Parallel Fan-Out

This pattern prepares shared input, runs independent checks in parallel, and
then summarizes.

```json
{
  "name": "fanout-demo",
  "mode": "serial",
  "tasks": [
    {
      "name": "prepare",
      "command": "mkdir -p work && echo ready > work/input.txt"
    },
    {
      "name": "run checks",
      "workflow": {
        "name": "checks",
        "mode": "parallel",
        "tasks": [
          {"name": "bytes", "command": "wc -c work/input.txt"},
          {"name": "hash", "command": "sha256sum work/input.txt"}
        ]
      }
    },
    {
      "name": "summarize",
      "command": "echo checks complete"
    }
  ]
}
```

The generated workflow directory includes a nested `checks/run.sh`.

## Slurm Workflow

Slurm rendering keeps the workflow control scripts as bash and submits command
tasks with `sbatch --wait`.

```json
{
  "name": "slurm-demo",
  "mode": "serial",
  "tasks": [
    {
      "name": "preprocess",
      "command": "python preprocess.py data/input.tsv",
      "slurm": {
        "time": "00:15:00",
        "cpus-per-task": "2",
        "mem": "4G"
      }
    },
    {
      "name": "compute",
      "command": "python compute.py work/preprocessed.tsv",
      "slurm": {
        "time": "02:00:00",
        "cpus-per-task": "16",
        "mem": "64G"
      }
    }
  ]
}
```

Build:

```bash
orchestrator build slurm-demo.json --target slurm --out-dir build/slurm
```

Run the generated control script from a login node or submission environment:

```bash
bash build/slurm/slurm-demo/run.sh
```

## Custom Mediator

Pass a different mediation command when rendering:

```bash
orchestrator build fanout-demo.json \
  --mediator "python scripts/mediate.py" \
  --out-dir build/workflows
```

The generated task scripts append the standard mediation arguments after your
command:

```bash
python scripts/mediate.py --workflow fanout-demo --task prepare --status "$status" --state-dir "$STATE_DIR"
```
