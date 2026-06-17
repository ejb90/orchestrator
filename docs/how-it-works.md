# How It Works

Orchestrator is intentionally script-first. It does not create a scheduler DAG,
database state machine, or long-running controller process.

Instead, it renders executable files that make the workflow explicit on disk.

## Build Phase

The `build` command reads a workflow JSON file and writes:

- one `run.sh` for each workflow
- one task script for each command task
- nested workflow directories for sub-workflows

For a workflow named `demo`, the output can look like this:

```text
build/workflows/demo/
  run.sh
  01-prepare.sh
  02-fanout/
    parallel-work/
      run.sh
      01-alpha.sh
      02-beta.sh
  03-summarize.sh
```

Names are normalized into stable lowercase path segments. Task order is encoded
with numeric prefixes.

## Execution Phase

The generated `run.sh` is responsible for the workflow's immediate children.

In `serial` mode it runs each child in order:

```bash
should_continue || exit 1
STATE_DIR="$STATE_DIR" bash "$SCRIPT_DIR"/01-prepare.sh

should_continue || exit 1
STATE_DIR="$STATE_DIR" bash "$SCRIPT_DIR"/02-fanout/parallel-work/run.sh
```

In `parallel` mode it starts every child and then waits:

```bash
STATE_DIR="$STATE_DIR" bash "$SCRIPT_DIR"/01-alpha.sh & pid_1=$!
STATE_DIR="$STATE_DIR" bash "$SCRIPT_DIR"/02-beta.sh & pid_2=$!

status=0
for pid in $pid_1 $pid_2; do
  wait "$pid" || status=$?
done
exit "$status"
```

Every child receives the same `STATE_DIR`, so nested workflows see the same stop
file and event log.

## Task Phase

A command task script has three responsibilities:

1. Run the command.
2. Preserve the command's exit status.
3. Call the mediator with workflow, task, status, and state directory.

The simplified shape is:

```bash
status=0
{
python run.py
} || status=$?

orchestrator mediate --workflow demo --task compute --status "$status" --state-dir "$STATE_DIR"
```

If the task command fails, the generated script exits with the task status after
mediation runs.

## Slurm Target

For `--target slurm`, command task scripts include `#SBATCH` directives and the
parent workflow script submits them with:

```bash
STATE_DIR="$STATE_DIR" sbatch --wait "$SCRIPT_DIR"/01-compute.sh
```

Nested workflow runners are still invoked with `bash`, because they are workflow
control scripts rather than Slurm job scripts.
