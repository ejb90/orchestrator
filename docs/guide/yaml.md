# YAML workflow definitions

Use `.yaml` or `.yml` files for static workflows with reusable script, command, or
importable Python tasks. YAML supports comments and multiline scripts without requiring
a Python definition file. It builds the same workflow specification as the Python API,
using the same compiler, validation, execution, and recovery behavior.

```yaml
version: 1
name: report
tasks:
  write_report:
    script:
      body: |
        mkdir -p results
        printf '%s\n' "$GREETING" > results/report.txt
    env:
      GREETING: Hello from YAML
    outputs:
      - name: report
        kind: file
        source: results/report.txt
nodes:
  write:
    task: write_report
outputs:
  report: {$ref: write.report}
```

Save this as `workflow.yaml`, then use the existing CLI commands:

```bash
uv run orchestrator validate workflow.yaml
uv run orchestrator plan workflow.yaml
uv run orchestrator plan workflow.yaml --format table
uv run orchestrator plan workflow.yaml --tree
uv run orchestrator render workflow.yaml --output preview/
uv run orchestrator run workflow.yaml --allow-local
```

The repository includes a runnable example at `examples/script.yaml`.
`resume` uses the stored specification; it does not reload the YAML file. Start a new
run to use edits. YAML format `version: 1` is independent of compiled workflow schema v4.
`plan --format table` shows named steps, dependencies, and execution settings in the
terminal; the default plan output remains JSON. See the [terminal view](dags.md#terminal-dependency-table).
`plan --tree` instead shows prerequisites as branches, with shared dependencies marked.

??? info "Theory: One compiler, two authoring frontends"

    YAML describes data; the loader constructs the same builders as the Python API.
    Both feed one validation/compiler pipeline, so a YAML definition does not need a second
    scheduler or a separate interpretation of dependencies. Compilation precedes rendering
    of a real attempt with its identity and working paths.

    Read more: [Authoring and compilation](../fundamentals/definitions.md#author-compile-render-execute).

## Tasks, steps, and workflow outputs

`tasks` defines reusable work and its contracts; `nodes` creates named steps using
those definitions. Each step can supply its own bindings and overrides.

??? info "Theory: Reuse does not share execution identity"

    `tasks` defines reusable work: commands, environment, input/output contracts, and defaults.
    `nodes` places that work in the workflow as named steps (also called invocations). A task
    definition does not run until a node uses it. Each step has its own inputs, dependencies,
    resource overrides, execution history, and isolated attempts; retries add attempts to
    that same step.

    Read more: [Definitions, steps, runs, and attempts](../fundamentals/definitions.md#a-definition-is-not-an-execution).

In the example above, `write_report` is the task definition and `write` is the step that
uses it. The task declares an output named `report`. `{$ref: write.report}` means
"the output named `report` from the step named `write`". The top-level `outputs.report`
exposes that result as the workflow's public `report` output. It does not create another
task or copy the file again.

The workflow's output name can differ from the task's output name. To run the same task
twice, replace the example's `nodes` and top-level `outputs` with:

```yaml
nodes:
  first_report:
    task: write_report
  second_report:
    task: write_report
outputs:
  first_document: {$ref: first_report.report}
  second_document: {$ref: second_report.report}
```

These are independent steps, eligible to run in parallel. Each writes to its own attempt
directory, so their relative `results/report.txt` paths are distinct. Add
`after: [first_report]` to `second_report` if it must wait for the first step to succeed.

## Fields and bindings

| Location | Supported fields |
| --- | --- |
| Workflow | `version` (required: `1`), `name` (required), `inputs`, `resources`, `max_active`, `directory`, `tasks`, `nodes` (required), `outputs` |
| Task definition in `tasks` | Exactly one of `script`, `command`, `python`; plus `inputs`, `outputs`, `env`, `resources`, `retry`, `directory` |
| Node in `nodes` | `task` (required task name), `inputs`, `after`, `resources`, `trigger`, `finalizer`, `directory` |
| Resources | `cpus`, `timeout_seconds` |
| Retry | `max_attempts`, `backoff_seconds`, `categories` |
| Script | `body` or `path`, `shell`, `interpreter`, `setup`, `inherit_env`, `backend`, `slurm` |

Task `inputs` and `outputs` are lists of mappings using the same fields and defaults as
[`TaskInput` and `TaskOutput`](tasks.md). Workflow `inputs` is a list of names; node
`inputs` and workflow `outputs` are mappings of names to literals or bindings.

`{$input: name}` binds a declared workflow input. `{$ref: node.output}` binds a named
output and automatically adds the dependency. These bindings can appear inside lists
and mappings. Other values are literal JSON-compatible data. There is no expression
evaluation, interpolation, or environment-variable expansion in YAML fields.

```yaml
version: 1
name: double_twice
inputs: [start]
tasks:
  double:
    python: examples.tasks:double
    inputs:
      - name: value
    outputs:
      - name: value
nodes:
  first:
    task: double
    inputs:
      value: {$input: start}
  second:
    task: double
    inputs:
      value: {$ref: first.value}
outputs:
  value: {$ref: second.value}
```

From the repository root, run with `--inputs '{"start": 3}'` to obtain `12`.
Python operations and validators must be importable in the task environment, just as
with Python-authored workflows. Scripts, commands, and Python references execute the
operations you declare when the workflow runs.

Independent nodes run in parallel subject to concurrency limits. Use `after: [first]`
for a control dependency without a data binding. Task definitions can be reused by
multiple nodes. Resource precedence follows the existing Python API. Required finalizers
use `finalizer: true` with `trigger: all_terminal` and explicit `after` dependencies.

`command` is an argument list, such as `[printf, hello]`; use `script.body: |` for shell
command sequences. `script` supports the same [shell and Slurm settings](scripts.md)
as the Python API, including experimental Slurm placement.

## Paths and validation

`directory` accepts a path string or `{path: scratch, relative_to: root}`. Absolute,
run-root-relative, and parent-workflow-relative bases retain isolated attempts. See
[execution directories](directories.md) for inheritance, retries, and resume semantics.

Relative `script.path` values and literal artifact input source paths in the YAML file
resolve against its containing directory. Output source paths and relative staging or
collection destinations remain relative to each attempt. Artifact paths supplied through
`--inputs` retain the Python compiler's working-directory behavior; use absolute paths
when passing them from another directory. Application command paths follow the attempt's
working directory. Script source content is captured during compilation.

Unknown fields, duplicate mapping keys, aliases/merge keys, custom object tags, non-string
mapping keys, and non-JSON values are rejected. Quote environment and Slurm option values
that should be strings, for example `OMP_NUM_THREADS: "4"` and `nodes: "1"`. PyYAML also
interprets unquoted `on`, `off`, `yes`, `no`, and dates specially; quote them when intended
as strings. Cycles, missing dependencies, and invalid bindings use the common compiler's
validation rules.

This initial format covers flat static graphs. Use the Python API for nested workflows,
decisions, branch selection, `Select`, and bounded iteration. Includes, templating, and
dynamic graph expansion are not part of YAML format v1.

The Python entry point `load_yaml(path, inputs={...})`, exported by `orchestrator`, returns
the compiled specification for use with `create_run` and `Coordinator`.
