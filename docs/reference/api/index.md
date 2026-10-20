# API reference

These pages are generated from Python source by mkdocstrings during every MkDocs build.
They include classes, functions, methods, constructor signatures, type annotations,
docstrings, and expandable source code. Members without docstrings remain listed;
private helpers are included for contributors. Changes to existing module members are
picked up automatically.

Start with [Workflow][orchestrator.api.Workflow] and [Task][orchestrator.api.Task], then
[compile_workflow][orchestrator.spec.compile_workflow],
[load_yaml][orchestrator.yaml_loader.load_yaml],
[create_run][orchestrator.engine.create_run], and [Coordinator][orchestrator.engine.Coordinator].
The public authoring and execution API is re-exported
from `orchestrator`, so imports such as `from orchestrator import Workflow, Task` work.
Implementation helpers and private methods are internal and may change.

??? info "Theory: Signatures describe an interface, contracts establish outcomes"

    The generated signatures and annotations describe how to construct and call
    objects. Task input/output contracts and the coordinator state machine establish
    which results may be accepted. Internal helpers are documented without making
    their signatures a stable public promise.

    Read more: [Data and execution contracts](../../fundamentals/contracts.md).

| Module | Contents |
| --- | --- |
| [Workflow builders](api.md) | Tasks, scripts, ports, workflow steps, resources, retry policies, and iteration |
| [Specification and compiler](spec.md) | Compilation, serialization, digests, and validation |
| [YAML loader](yaml_loader.md) | Static YAML authoring and typed builder conversion |
| [Coordinator](engine.md) | Run creation, readiness, decisions, scheduling, and recovery |
| [Execution backends](backends.md) | Backend protocol, local processes, experimental Slurm, and routing |
| [Script rendering](scripts.md) | Source capture, shell quoting, retained scripts, and hash verification |
| [Artifact lifecycle](artifacts.md) | Input staging, output validation, and collection |
| [Worker runner](runner.md) | Callable loading, process identity, and result publication |
| [Persistence](storage.md) | SQLite snapshots/events and coordinator ownership |
| [Execution paths](paths.md) | Directory anchors, resolved bases, and isolated attempt locations |
| [Terminal views](presentation.md) | Rich dependency trees and tables |
| [CLI](cli.md) | Definition loading, commands, and presentation helpers |

For complete examples, see the [quick start](../../getting-started/quickstart.md),
[YAML guide](../../guide/yaml.md), and [workflow guide](../../guide/workflows.md).
Source collection uses static analysis rather than running workflow commands.
