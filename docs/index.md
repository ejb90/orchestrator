# Orchestrator

Orchestrator separates workflow definitions from execution. Python or static YAML builds a versioned
specification; a single coordinator schedules subprocess runners and records decisions,
attempts, results, and events.

This version supports **Linux local execution** with retained Bash/Zsh/Fish job files
and an experimental Slurm adapter. Cluster qualification, remote staging, and service
hosting remain future milestones.

- [Quick start](getting-started/quickstart.md): install, run an example, and write a workflow.
- [YAML definitions](guide/yaml.md): static text input, reusable tasks, named steps, and output bindings.
- [Workflow combinations](guide/workflows.md): serial and parallel work, nesting, branches,
  bounded iteration, retries, and finalizers.
- [Workflow DAGs](guide/dags.md): diagrams and Graphviz `.dot` sources for runnable examples.
- [Script tasks and Slurm](guide/scripts.md): self-describing job files, environments, and shell commands.
- [Tasks and artifacts](guide/tasks.md): input/output contracts for scripts, Python callables, and commands.
- [Operations and recovery](guide/operations.md): CLI commands, durable state, and restart behavior.
- [Architecture and status](reference/architecture.md): state transitions, assumptions, and limitations.
- [Development and CI](reference/development.md): locked dependencies, local checks, and GitHub Actions coverage.

![Diamond workflow DAG](assets/dags/diamond.svg)
