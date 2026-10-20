# Fundamentals

Orchestrator separates describing work, deciding when it may run, and actually executing
it. These pages explain why, starting with ordinary scripts and building up to graphs,
contracts, state machines, and recovery. No prior workflow-engine knowledge is required.

| Read | Question it answers |
| --- | --- |
| [Philosophy](philosophy.md) | Why use an orchestrator, and what should it take responsibility for? |
| [Definitions and compilation](definitions.md) | How do a reusable task, a step, a run, and an attempt differ? |
| [Graphs and control flow](graphs.md) | How do dependencies, parallel work, branches, and loops fit together? |
| [Data and execution contracts](contracts.md) | How do files, validation, scripts, and directories define successful work? |
| [Durable execution and recovery](durability.md) | What can be known after a failure, and when is retrying safe? |

The [sensor-report quick start](../getting-started/quickstart.md) is the running example:
a cleaner consumes a CSV, and a reporter consumes the cleaner's output. Each concept is
also useful for simulations, preprocessing pipelines, and collections of terminal commands.

If you want to run something first, follow the quick start and return here as needed.
The user guides contain collapsed **Theory** boxes linking to the relevant explanation;
expanding them is optional. Commands, configuration rules, and operational warnings remain
in the normal guide flow.

The [architecture reference](../reference/architecture.md) describes the current modules,
transition tables, and implementation status. These fundamentals explain the reasoning
behind them; they do not imply support for future features. In particular, YAML currently
describes flat static graphs, Python adds nesting and conditional controls, Slurm remains
experimental, and arbitrary runtime graph expansion is not implemented.
