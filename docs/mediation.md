# Mediation

Mediation is the point where a completed task hands control back to workflow
policy.

The generated scripts always call a mediator after a task command exits. The
mediator receives enough information to decide whether execution should
continue:

| Argument | Meaning |
| --- | --- |
| `--workflow` | Name of the workflow that owns the task. |
| `--task` | Name of the task that just finished. |
| `--status` | Exit status from the task command. |
| `--state-dir` | Shared state directory for the workflow run. |

## Default Mediator

The default command is:

```bash
orchestrator mediate
```

It writes one JSON object per event to:

```text
$STATE_DIR/events.jsonl
```

For a successful task it returns `0`.

For a failed task it behaves like this:

| Context | Behavior |
| --- | --- |
| Interactive terminal | Ask whether to continue. |
| Non-interactive terminal | Write `stop` and return the failed status. |
| `--on-failure continue` | Continue without writing `stop`. |
| `--on-failure stop` | Write `stop` immediately. |

When a stop decision is made, the mediator writes:

```text
$STATE_DIR/stop
```

Serial workflow scripts check this file before launching each child task.

## Event Log

An event log entry looks like:

```json
{
  "status": 0,
  "task": "prepare",
  "time": "2026-06-17T12:00:00+00:00",
  "workflow": "demo"
}
```

The log is append-only JSON Lines, which makes it easy to inspect with shell
tools or load into Python.

## Custom Mediators

Use `--mediator` to replace the default mediation command:

```bash
orchestrator build workflow.json --mediator "python scripts/mediate.py"
```

The command should accept:

```text
--workflow NAME --task NAME --status STATUS --state-dir PATH
```

Return codes define the control decision:

| Return code | Meaning |
| --- | --- |
| `0` | Continue if the task command succeeded. |
| non-zero | Treat mediation as failed. |

For failed task commands, the generated task script exits with the original task
status after the mediator runs. This keeps the failure visible to the parent
workflow while still giving the mediator a chance to record state or prompt the
user.
