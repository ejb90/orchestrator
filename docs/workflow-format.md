# Workflow Format

Workflow files are JSON objects with a small schema.

## Workflow Object

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string | yes | Human-readable workflow name. Also used to create script paths. |
| `mode` | string | no | `serial` or `parallel`. Defaults to `serial`. |
| `tasks` | array | yes | Ordered list of task objects. Must contain at least one task. |

Example:

```json
{
  "name": "analysis",
  "mode": "serial",
  "tasks": [
    {"name": "prepare", "command": "echo ready"}
  ]
}
```

## Task Object

Each task must define exactly one of `command` or `workflow`.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string | yes | Human-readable task name. |
| `command` | string | conditional | Shell command to run for a leaf task. |
| `workflow` | object | conditional | Nested workflow object. |
| `slurm` | object | no | Slurm options for command tasks when rendering with `--target slurm`. |

Command task:

```json
{
  "name": "count rows",
  "command": "wc -l data/input.tsv"
}
```

Nested workflow task:

```json
{
  "name": "fanout",
  "workflow": {
    "name": "parallel checks",
    "mode": "parallel",
    "tasks": [
      {"name": "check a", "command": "python check.py a"},
      {"name": "check b", "command": "python check.py b"}
    ]
  }
}
```

Slurm task:

```json
{
  "name": "align sample",
  "command": "python align.py sample.fastq",
  "slurm": {
    "time": "01:00:00",
    "cpus-per-task": "8",
    "mem": "16G"
  }
}
```

Slurm option keys can be written with or without the leading `--`. The renderer
adds the prefix when needed.

## Validation Rules

The model enforces:

- workflow names cannot be empty
- workflows must contain at least one task
- task names cannot be empty
- each task must define exactly one of `command` or `workflow`
- `mode` must be `serial` or `parallel`
- `slurm` must be a JSON object when present
