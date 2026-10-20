# Workflow composition

See the [workflow DAG gallery](dags.md) for visualizations and downloadable `.dot`
sources corresponding to the runnable examples below.

Python definition files export a `Workflow` named `workflow`. Importing the builder performs no
submission. The CLI executes definition files as trusted Python, so top-level code in
those files can still have side effects. Runtime decisions belong in explicit policies;
ordinary Python `if` statements only choose what graph gets built.

For flat static workflows, [YAML definitions](yaml.md) provide a text format that uses
the same compiler and execution engine. The examples below demonstrate the Python API.

A **task definition** describes reusable work and its contracts. A **step** (or invocation)
uses that definition at a named place in a workflow with its own bindings and dependencies.
Multiple steps may use the same task. Each executed step has its own attempt history;
retries create additional attempts for that step. In Python, `workflow.add("write", report)`
creates a step; in YAML, the equivalent is `nodes.write.task: report`. Workflow outputs
explicitly expose selected step outputs to callers.

Names must start with a letter and contain only letters, digits, `_`, or `-`.
Invocation names must be unique within their workflow. Compiled IDs retain scopes, such
as `experiment/optimize/step_4`. The compiler adds logical `@enter` and `@exit` nodes;
these do not consume workers.

## Serial, parallel, and diamond graphs

```python
from orchestrator import Task, TaskInput, TaskOutput, Workflow

number = Task(
    python="examples.tasks:number",
    inputs=[TaskInput("value")],
    outputs=[TaskOutput("value")],
)
workflow = Workflow("parallel")
a = workflow.add("a", number, inputs={"value": 1})
b = workflow.add("b", number, inputs={"value": 2})
c = workflow.add("c", number, inputs={"value": a.out("value")}, after=(b,))
workflow.expose(value=c.out("value"))
```

`a` and `b` can execute concurrently; `c` waits for both. References add data dependencies;
`after` adds control-only dependencies. You may also call `workflow.serial(a, b, c)` to
chain already-added nodes, or `workflow.parallel(a, b, after=(seed,))` to add common
prerequisites. These helpers only add edges; `parallel` does not remove existing edges.
A cycle is rejected at compilation.

See `examples/diamond.py`: two independent transformations share a seed, then their
results feed a join. Parallel eligibility is subject to global and scope limits.

## Reusable nested workflows

```python
from orchestrator import Input, Task, TaskInput, TaskOutput, Workflow

double = Task(
    python="examples.tasks:double",
    inputs=[TaskInput("value")],
    outputs=[TaskOutput("value")],
)
child = Workflow("transform", inputs=("value",), max_active=1)
a = child.add("a", double, inputs={"value": Input("value")})
b = child.add("b", double, inputs={"value": a.out("value")})
child.expose(value=b.out("value"))

workflow = Workflow("parent")
left = workflow.add("left", child, inputs={"value": 2})
right = workflow.add("right", child, inputs={"value": 5})
workflow.expose(left=left.out("value"), right=right.out("value"))
```

Children expand into one graph but keep separate scopes. Callers bind only exposed child
outputs. A child invocation completes after its children and required finalizers resolve.
One child finishing early does not close its parent or sibling. Nested workflows themselves
use no subprocess. The same definition can be invoked repeatedly with different inputs.

`max_active` counts active executable descendants, including descendants of nested children.
An outer limit and each inner limit must all permit a launch. The global `--max-active`
limit also applies. A scope limit is not a resource reservation.

## Branches and joins

An importable policy receives resolved keyword arguments and returns an action dictionary:

```python
def choose(value):
    return {"action": "branch", "branch": "large" if value >= 10 else "small"}
```

Use `when` to guard tasks **or child workflows**. Use `Select` to express optional or
alternative outputs, in priority order:

```python
from orchestrator import Select

choice = workflow.decision("choose", "examples.tasks:choose", inputs={"value": 12})
large = workflow.add("large", number, inputs={"value": 100}, when=(choice, "large"))
small = workflow.add("small", number, inputs={"value": 1}, when=(choice, "small"))
join = workflow.add(
    "join",
    number,
    inputs={"value": Select((large.out("value"), small.out("value")), default=0)},
    trigger="selected_success",
)
```

A decision can depend on multiple candidate results. It waits for every declared input,
so completion order does not change its evidence. Policies run synchronously in the
coordinator; they should be quick and deterministic and must not submit work or inspect
mutable external state. Expensive evaluations should be tasks.

| Trigger | When dependencies are terminal | Typical use |
| --- | --- | --- |
| `all_success` (default) | Run only if all succeeded | Ordinary task/data edges |
| `all_terminal` | Run after success, failure, cancellation, or skipping | Finalizers; failure-tolerant processing |
| `selected_success` | Ignore skipped dependencies; block on failed or cancelled ones | Conditional join |

`Select` waits for **all** its referenced producers to become terminal and then chooses
the first successful available output. It is not a race or first-completion primitive.
If none is available, its default (normally `None`) is used. A `selected_success` join
with all branches skipped can therefore run with its default. Required guarded outputs
need a matching guard or `Select`; invalid conditional joins are rejected.
`Select` may also appear in workflow output bindings. Task code must handle optional values.

A guard on one task does not automatically guard other tasks: repeat the guard for
additional branch tasks, or put the whole branch inside a guarded child workflow.

## Bounded iteration and early completion

```python
from orchestrator import Handle, Task, TaskInput, TaskOutput, Workflow, iterate

step = Task(
    python="examples.tasks:increment",
    inputs=[TaskInput("value")],
    outputs=[TaskOutput("value")],
)
optimization = iterate(
    "optimization",
    step,
    initial=0,
    max_iterations=6,
    policy="examples.tasks:converge",
)
optimization.add(
    "cleanup",
    Task(python="examples.tasks:cleanup", outputs=[TaskOutput("report", kind="file")]),
    after=tuple(Handle(node.id) for node in optimization.nodes),
    trigger="all_terminal",
    finalizer=True,
)
workflow = Workflow("experiment")
optimized = workflow.add("optimize", optimization)
workflow.expose(value=optimized.out("value"))
```

The helper unrolls one state-carrying task per iteration. `input_name` and `output_name`
default to `value`; its workflow exposes `value`. The policy receives `value` and the
1-based `iteration` after every step, including the last. For example:

```python
def converge(value, iteration):
    if value >= 4:
        return {
            "action": "finish",
            "outputs": {"value": value},
            "reason": f"Converged at iteration {iteration}",
        }
    return {"action": "continue"}
```

This stops at step four. Steps five and six have no attempts, their nodes are skipped,
the finalizer runs, and the child returns `4`. The runnable `examples/convergence.py`
adds a parent report task that doubles this result to `8`.

If no decision finishes early, all bounded steps run and the last value is exposed.
For a multi-task iteration body, construct a finite sequence of nested workflow
invocations and `decision` barriers manually; the helper currently accepts one `Task`.
A fixed batch or parameter sweep is simply a Python loop adding independent invocations
at definition time. Runtime graph expansion is not supported.

| Policy action | Payload | Effect |
| --- | --- | --- |
| `continue` | Optional `reason` | Open the next dependency barrier |
| `branch` | `branch` string | Select matching guards |
| `finish` | Complete `outputs` dictionary; optional `reason` | Close the current scope successfully |
| `fail` | Optional `reason` | Fail and close the current scope |
| `cancel` | Optional `reason` | Request cancellation of the whole run |

A `finish` payload must supply all exposed output names. Already-running work drains;
unstarted ordinary work in that scope is skipped. Required finalizers remain eligible.
An already-running task or required finalizer that fails still fails the scope. Scope
completion waits for all descendants; committed decisions cannot be reversed by late results.

## Finalizers, retries, and combinations

Finalizers are executable tasks with `finalizer=True`, `trigger="all_terminal"`, and
explicit `after` dependencies on the work they clean up. The flag does **not** automatically
add a barrier. Use `Select` for any inputs that might be unavailable after failure.
Finalizers in a branch never entered are skipped. Required finalizer failure fails its scope.
Cancellation preserves eligible finalizers. Nested workflows cannot themselves be finalizers
in this version. Coordinator loss delays finalization until resume.

```python
from orchestrator import Resources, Retry, Task, TaskInput, TaskOutput

robust = Task(
    python="my_tasks:simulate",
    inputs=[TaskInput("value")],
    outputs=[TaskOutput("value")],
    retry=Retry(
        max_attempts=3, backoff_seconds=2, categories=("application", "timeout")
    ),
    resources=Resources(cpus=1, timeout_seconds=60),
)
```

`max_attempts` includes the initial attempt. Each retry gets fresh inputs and a new directory.
Downstream nodes remain blocked until success or exhausted retries. Retries can duplicate
external side effects, so task authors must account for that.

| Combination | How to build it |
| --- | --- |
| Parallel tasks followed by a serial stage | References/`after` from every producer to the next stage |
| Parallel child pipelines | Add multiple instances of a reusable workflow |
| Branch around a whole pipeline | Guard a nested workflow invocation |
| Join alternative branches | `Select` plus `selected_success` |
| Continue after an optional failed task | `Select` plus `all_terminal`; the overall scope still records the failure |
| Parallel independent optimizations | Add multiple `iterate(...)` workflows to one parent |
| Conditional optimization | Guard the optimization child, join its output with `Select` |
| Cleanup after convergence, failure, or cancellation | Required task finalizer with explicit terminal dependencies |
| Retry a task inside a branch or loop | Attach `Retry` to its reusable task definition |
| Limit nested parallel workloads | Combine `Workflow(max_active=...)` with global `--max-active` |

A failed ordinary task is not erased by a tolerant join: the enclosing scope still fails.
For workloads where “no candidate found” is a successful outcome, return a normal result
containing that condition and let a policy choose what to do next.
