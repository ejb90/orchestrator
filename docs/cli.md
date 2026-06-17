# CLI Reference

The package exposes the `orchestrator` command.

```bash
orchestrator --help
```

## `orchestrator build`

Render a workflow JSON file into scripts.

```bash
orchestrator build WORKFLOW [OPTIONS]
```

Options:

| Option | Default | Description |
| --- | --- | --- |
| `-o, --out-dir PATH` | `build/workflows` | Directory where scripts are written. |
| `--target bash` | `bash` | Render local bash task scripts. |
| `--target slurm` | `bash` | Render Slurm job command files instead. |
| `--mediator COMMAND` | `orchestrator mediate` | Mediation command appended to each task script. |

Example:

```bash
orchestrator build workflow.json --target bash --out-dir build/workflows
```

The command prints every file it writes.

## `orchestrator mediate`

Run the default mediator.

```bash
orchestrator mediate --workflow NAME --task NAME --status STATUS --state-dir PATH
```

Options:

| Option | Default | Description |
| --- | --- | --- |
| `--workflow NAME` | required | Owning workflow name. |
| `--task NAME` | required | Completed task name. |
| `--status STATUS` | required | Task command exit status. |
| `--state-dir PATH` | required | Shared state directory. |
| `--on-failure ask` | `ask` | Failure policy: `ask`, `stop`, or `continue`. |

Example:

```bash
orchestrator mediate \
  --workflow demo \
  --task prepare \
  --status 0 \
  --state-dir build/workflows/demo/.orchestrator-state
```

## Module Execution

During local development you can run the CLI without installing the script:

```bash
python -m orchestrator --help
python -m orchestrator build workflow.json
```
