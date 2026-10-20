"""Definition-only builders. Constructing a workflow never starts work."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Ref:
    """Reference a named output of a step in the current workflow scope.

    Attributes:
        node: Invocation ID, not the reusable task definition's name.
        output: Output port exposed by that invocation.
    """

    node: str
    output: str


@dataclass(frozen=True)
class Input:
    """Bind a task or child workflow argument to a named workflow input.

    Attributes:
        name: Name declared in the containing workflow's input contract.
    """

    name: str


@dataclass(frozen=True)
class Select:
    """First available output, in priority order; default if all are absent."""

    refs: tuple[Ref, ...]
    default: Any = None


@dataclass(frozen=True)
class Resources:
    """Execution resource defaults; overrides replace the complete resource object.

    Attributes:
        cpus: Positive CPU count. Informational locally; supplies Slurm's CPUs per task
            when the script has no explicit scheduler setting for it.
        timeout_seconds: Positive runner timeout, or None for no runner deadline.
            This is separate from the Slurm wall-time request.
    """

    cpus: int = 1
    timeout_seconds: float | None = None


@dataclass(frozen=True)
class Retry:
    """Retry only confirmed failures in the declared categories.

    Attributes:
        max_attempts: Total attempts allowed, including the first execution.
        backoff_seconds: Fixed delay before starting a retry.
        categories: Retryable failure categories: application and/or timeout.
            Uncertain submissions are never automatically retried.
    """

    max_attempts: int = 1
    backoff_seconds: float = 0
    categories: tuple[str, ...] = ("application",)


@dataclass(frozen=True)
class TaskInput:
    """Named value or staged artifact; invocation bindings override source defaults.

    Attributes:
        name: Unique input name, passed as a keyword argument to Python tasks.
        kind: value, file, or directory.
        source: Default literal or binding; None means the required input is unbound.
        destination: Attempt-relative staging path; defaults to inputs/NAME for artifacts.
        mode: copy for isolation, or link to share the source artifact.
        essential: Require a supplied value and, for artifacts, an existing source.
        default: Fallback for an unbound optional value. Optional artifacts use None.
    """

    name: str
    kind: str = "value"
    source: Any = None
    destination: str | None = None
    mode: str = "copy"
    essential: bool = True
    default: Any = None


@dataclass(frozen=True)
class TaskOutput:
    """Collection and validation contract, independent of workflow decisions.

    Attributes:
        name: Unique output port available through a step's handle.
        kind: value, file, or directory.
        source: Static attempt-relative artifact path, or None for a task-returned value/path.
        destination: Optional collection path; relative paths preserve attempt isolation.
        mode: copy or link when collecting an artifact.
        essential: Fail the attempt if this output is missing.
        validator: Optional importable module:function returning True to accept the output.
    """

    name: str
    kind: str = "value"
    source: str | None = None
    destination: str | None = None
    mode: str = "copy"
    essential: bool = True
    validator: str | None = None


@dataclass(frozen=True)
class Script:
    """Shell program captured at compilation; setup uses the selected shell's syntax.

    Attributes:
        body: Inline script text; choose exactly one of body and path.
        path: UTF-8 source file captured during compilation.
        shell: bash, zsh, or fish; authored error handling follows that shell's rules.
        interpreter: Absolute shell executable, or None to resolve it during compilation.
        setup: Shell-specific environment initialization before application commands.
        inherit_env: Inherit the worker environment, or start the application with a clean base.
        backend: local or experimental slurm placement.
        slurm: Supported static scheduler options, with string values.
    """

    body: str | None = None
    path: str | None = None
    shell: str = "bash"
    interpreter: str | None = None
    setup: str = ""
    inherit_env: bool = True
    backend: str = "local"
    slurm: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Directory:
    """Base directory for isolated attempts, resolved once when the run is created.

    Attributes:
        path: Absolute path, or a path relative to the selected anchor.
        relative_to: absolute, root (the individual run directory), or parent (the
            enclosing workflow's resolved base). None infers absolute for absolute
            paths and parent otherwise. This never selects an exact shared task cwd.
    """

    path: str
    relative_to: str | None = None


@dataclass(frozen=True)
class Task:
    """Reusable executable definition, independent of named workflow invocations.

    Exactly one of python, command, and script must be supplied. Creating a Task does
    not execute it; Workflow.add places it in a graph. Every execution attempt has
    isolated paths, staged inputs, logs, and a validated result.

    Attributes:
        python: Importable module:function reference for a Python operation.
        command: Executable and argument tuple, without implicit shell interpretation.
        inputs: Named value and artifact input contracts.
        outputs: Named value and artifact output contracts.
        env: Explicit string environment variables retained in the run definition.
        resources: Task defaults overriding inherited workflow resources.
        retry: Confirmed-failure retry policy.
        script: Captured shell program definition.
        directory: Attempt base overriding the enclosing workflow's directory.
    """

    python: str | None = None
    command: tuple[str, ...] | None = None
    inputs: list[TaskInput] = field(default_factory=list)
    outputs: list[TaskOutput] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    resources: Resources | None = None
    retry: Retry = field(default_factory=Retry)
    script: Script | None = None
    directory: Directory | str | None = None


@dataclass(frozen=True)
class Handle:
    """Definition-time identity of a workflow step, used for references and dependencies."""

    id: str

    def out(self, name: str) -> Ref:
        """Reference an output port; compilation validates that the port exists."""
        return Ref(self.id, name)


@dataclass
class Invocation:
    """Internal placement record for a task, child workflow, or decision policy.

    Builders create these records; callers normally use Workflow.add or Workflow.decision.
    A policy is represented by an importable string rather than a Task definition.
    """

    id: str
    definition: Task | Workflow | str
    inputs: dict[str, Any]
    after: list[str]
    when: tuple[str, str] | None
    trigger: str
    finalizer: bool
    resources: Resources | None
    directory: Directory | str | None = None


@dataclass
class Workflow:
    """Compose named task and workflow invocations without executing them.

    Attributes:
        name: Workflow name; the root name forms the compiled node ID prefix.
        inputs: Names callers must bind when compiling or invoking the workflow.
        resources: Inherited resource defaults for contained work.
        max_active: Optional positive concurrency limit for this scope and descendants.
        nodes: Invocation records populated by the builder methods.
        outputs: Public output bindings, populated by expose.
        directory: Base for contained tasks; relative parent paths chain through nested workflows.
    """

    name: str
    inputs: tuple[str, ...] = ()
    resources: Resources | None = None
    max_active: int | None = None
    nodes: list[Invocation] = field(default_factory=list)
    outputs: dict[str, Any] = field(default_factory=dict)
    directory: Directory | str | None = None

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
        directory: Directory | str | None = None,
    ) -> Handle:
        """Place a reusable task or child workflow at a unique named step.

        Args:
            id: Step identity within this workflow scope.
            definition: Reusable Task or nested Workflow to invoke.
            inputs: Input bindings overriding task port source defaults.
            after: Additional control prerequisites; output bindings add edges automatically.
            when: Decision handle and branch label required to enable the step.
            trigger: all_success, selected_success, or all_terminal dependency rule.
            finalizer: Keep a required task eligible during scope closure/cancellation;
                requires all_terminal and cannot wrap a child workflow.
            resources: Invocation override replacing the inherited resource object.
            directory: Invocation override replacing the task/child-workflow directory default.

        Returns:
            Handle: Step identity for output references and further dependencies.
        """
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
                directory,
            )
        )
        return Handle(id)

    def decision(
        self, id: str, policy: str, *, inputs: dict[str, Any] | None = None, after: tuple[Handle, ...] = ()
    ) -> Handle:
        """Add a coordinator policy evaluated after its declared evidence is available.

        Args:
            id: Unique step ID in this workflow.
            policy: Importable module:function returning an action dictionary.
                Supported actions are continue, branch, finish, fail, and cancel.
            inputs: Explicit literal or output bindings passed to the policy.
            after: Additional control prerequisites.

        Returns:
            Handle: Decision identity for downstream branch guards and dependencies.
        """
        self.nodes.append(
            Invocation(id, policy, inputs or {}, [h.id for h in after], None, "all_success", False, None)
        )
        return Handle(id)

    def expose(self, **outputs: Any) -> Workflow:
        """Set the workflow's public output bindings and return this builder.

        Keyword names are the workflow output names. Binding a step output exposes
        its result without adding another execution or copying the artifact again.
        """
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
