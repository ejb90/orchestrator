# Graphs and control flow

## Dependencies form a directed acyclic graph

A graph consists of nodes and edges. Here, nodes represent tasks, decisions, and logical
workflow boundaries. An edge `A → B` says that B has A as a prerequisite. A **directed
acyclic graph**, or DAG, has no path that follows edges back to its starting node.

In the sensor workflow, `clean → report`. In a diamond:

```text
        ┌→ left  ─┐
seed ───┤         ├→ join
        └→ right ─┘
```

Both `left` and `right` need `seed`; `join` needs both results. A cycle such as
`clean → report → clean` would leave no first step able to satisfy its prerequisites,
so compilation rejects it.

## A graph describes a partial order

Dependencies constrain some ordering choices without prescribing one global sequence.
`left` may finish before or after `right`. A **topological order** lists every node
after its prerequisites, but several valid lists may exist for the same graph.

This distinction explains why listing one task before another in YAML or in a terminal
table does not add a dependency. Independent tasks may overlap if capacity allows it.
Concurrency means multiple operations can be in progress; parallelism means executing
at the same instant. Graph independence does not guarantee enough CPUs, workers, or
scheduler allocations for simultaneous execution.

## Readiness includes state and control rules

An edge alone does not say that successful output exists. The coordinator waits for
prerequisites to become terminal, evaluates the step's trigger and branch guard, and
checks scope closure, retry deadlines, and capacity before launching work.

`all_success` requires success from every prerequisite. `all_terminal` can allow work
after failure, cancellation, or skipping. `selected_success` tolerates skipped branches
but blocks on failed or cancelled prerequisites. Input bindings must still be resolvable.
An essential/optional file flag does not change these graph rules.

Policies receive declared inputs. Requiring all relevant evidence before deciding helps
keep a decision independent of observation arrival order. `Select` similarly waits for
its referenced producers to become terminal and then chooses the first available success
in declared reference order; it is not a first-to-finish race. Deterministic policy code
is an authoring requirement, not an automatic restriction on arbitrary Python behavior.

## Hierarchy adds scopes, not waiting workers

A nested workflow expands into the graph while keeping a logical scope. Its entry and
exit barriers establish when children may start and when public outputs can be returned.
The workflow itself does not occupy a worker while its children execute.

Global and scope concurrency limits all apply to a descendant task. These limits count
active attempts, including queued or unresolved work; they do not reserve a shared CPU allocation.
Closing a child scope does not close its parent or a sibling scope.

## Loops can have an acyclic representation

A bounded iteration can be unrolled into a finite sequence:

```text
step_1 → check_1 → step_2 → check_2 → ... → step_N → check_N
```

Each next step depends on the preceding decision. If the check after step four finishes
the scope, steps five and six remain unstarted. Already-submitted work drains and
required finalizers remain eligible before the scope completes.

An ordinary Python loop in a definition constructs graph nodes immediately. It cannot
wait on unfinished runtime results. Runtime conditions belong in explicit decisions.
Unbounded loops and arbitrary runtime graph expansion are outside the current scope.

## A tree is a view of a DAG

A dependency tree must repeat a shared prerequisite or refer back to it. In
`plan --tree`, children are prerequisites of their parent, and shared nodes expand once.
That repeated reference does not represent another task execution.

The tree and SVG diagrams collapse boundaries or redundant transitive edges to improve
readability. They preserve prerequisite reachability, but direct edges can still matter
to trigger behavior after skipping or failure. The full table and JSON remain the place
to inspect every direct dependency and barrier.

See [visualization examples](../guide/dags.md) and [composition recipes](../guide/workflows.md).
