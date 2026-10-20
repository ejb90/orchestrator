"""Definition-only builders. Constructing a workflow never starts work."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Ref:
    node: str
    output: str


@dataclass(frozen=True)
class Input:
    name: str


@dataclass(frozen=True)
class Select:
    """First available output, in priority order; default if all are absent."""

    refs: tuple[Ref, ...]
    default: Any = None


@dataclass(frozen=True)
class Resources:
    cpus: int = 1
    timeout_seconds: float | None = None


@dataclass(frozen=True)
class Retry:
    max_attempts: int = 1
    backoff_seconds: float = 0
    categories: tuple[str, ...] = ("application",)


@dataclass(frozen=True)
class TaskInput:
    """Named value or staged artifact; invocation bindings override source defaults."""

    name: str
    kind: str = "value"
    source: Any = None
    destination: str | None = None
    mode: str = "copy"
    essential: bool = True
    default: Any = None


@dataclass(frozen=True)
class TaskOutput:
    """Collection and validation contract, independent of workflow decisions."""

    name: str
    kind: str = "value"
    source: str | None = None
    destination: str | None = None
    mode: str = "copy"
    essential: bool = True
    validator: str | None = None


@dataclass(frozen=True)
class Script:
    """Shell program captured at compilation; setup uses the selected shell's syntax."""

    body: str | None = None
    path: str | None = None
    shell: str = "bash"
    interpreter: str | None = None
    setup: str = ""
    inherit_env: bool = True
    backend: str = "local"
    slurm: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Task:
    python: str | None = None
    command: tuple[str, ...] | None = None
    inputs: list[TaskInput] = field(default_factory=list)
    outputs: list[TaskOutput] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    resources: Resources | None = None
    retry: Retry = field(default_factory=Retry)
    script: Script | None = None


@dataclass(frozen=True)
class Handle:
    id: str

    def out(self, name: str) -> Ref:
        return Ref(self.id, name)


@dataclass
class Invocation:
    id: str
    definition: Task | Workflow | str
    inputs: dict[str, Any]
    after: list[str]
    when: tuple[str, str] | None
    trigger: str
    finalizer: bool
    resources: Resources | None


@dataclass
class Workflow:
    name: str
    inputs: tuple[str, ...] = ()
    resources: Resources | None = None
    max_active: int | None = None
    nodes: list[Invocation] = field(default_factory=list)
    outputs: dict[str, Any] = field(default_factory=dict)

    def add(
        self,
        id: str,
        definition: Task | Workflow,
        *,
        inputs: dict[str, Any] | None = None,
        after: tuple[Handle, ...] = (),
        when: tuple[Handle, str] | None = None,
        trigger: str = "all_success",
        finalizer: bool = False,
        resources: Resources | None = None,
    ) -> Handle:
        self.nodes.append(
            Invocation(
                id,
                definition,
                inputs or {},
                [h.id for h in after],
                (when[0].id, when[1]) if when else None,
                trigger,
                finalizer,
                resources,
            )
        )
        return Handle(id)

    def decision(
        self, id: str, policy: str, *, inputs: dict[str, Any] | None = None, after: tuple[Handle, ...] = ()
    ) -> Handle:
        """Policy returns {action: continue|branch|finish|fail|cancel, ...}."""
        self.nodes.append(
            Invocation(id, policy, inputs or {}, [h.id for h in after], None, "all_success", False, None)
        )
        return Handle(id)

    def expose(self, **outputs: Any) -> Workflow:
        self.outputs = outputs
        return self

    def serial(self, *handles: Handle) -> None:
        """Add edges between already-added invocations."""
        by_id = {node.id: node for node in self.nodes}
        for before, after in zip(handles, handles[1:]):
            by_id[after.id].after.append(before.id)

    def parallel(self, *handles: Handle, after: tuple[Handle, ...] = ()) -> None:
        """Give a group the same prerequisites; add no edges within the group."""
        by_id = {node.id: node for node in self.nodes}
        for handle in handles:
            by_id[handle.id].after.extend(h.id for h in after)


def iterate(
    name: str,
    task: Task,
    *,
    initial: Any,
    max_iterations: int,
    policy: str,
    input_name: str = "value",
    output_name: str = "value",
) -> Workflow:
    """Unroll a single-state iteration, with a coordinator decision after every step.

    The policy receives value and iteration (1-based). To converge, return
    {"action": "finish", "outputs": {"value": value}, "reason": "..."}.
    """
    if max_iterations < 1:
        raise ValueError("max_iterations must be positive")
    flow = Workflow(name)
    previous = initial
    barrier: tuple[Handle, ...] = ()
    for index in range(1, max_iterations + 1):
        step = flow.add(f"step_{index}", task, inputs={input_name: previous}, after=barrier)
        decision = flow.decision(
            f"check_{index}", policy, inputs={"value": step.out(output_name), "iteration": index}
        )
        previous = step.out(output_name)
        barrier = (decision,)
    return flow.expose(value=previous)
