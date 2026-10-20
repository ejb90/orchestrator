"""Versioned JSON specification and deterministic scope expansion."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .api import Input, Ref, Resources, Select, Task, TaskInput, TaskOutput, Workflow
from .paths import directory_spec, validate_directory

VERSION = 4
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(spec: dict) -> str:
    return hashlib.sha256(dumps(spec).encode()).hexdigest()


def references(value: Any) -> list[dict]:
    if isinstance(value, dict):
        if "$ref" in value:
            return [value]
        return [ref for item in value.values() for ref in references(item)]
    if isinstance(value, list):
        return [ref for item in value for ref in references(item)]
    return []


def compile_workflow(flow: Workflow, inputs: dict | None = None) -> dict:
    """Expand and validate a workflow as a deterministic, JSON-compatible specification.

    Args:
        flow: Root workflow builder; nested workflows expand into hierarchical node IDs.
        inputs: Bindings for all declared root workflow inputs.

    Returns:
        A schema-versioned dictionary containing expanded nodes, scopes, and the root exit.
        Script sources are captured; application commands are not executed.

    Raises:
        ValueError: Definitions, bindings, dependencies, resources, or script contracts are invalid.
    """
    nodes: dict[str, dict] = {}
    scopes: dict[str, dict] = {}
    visiting: set[int] = set()

    def expand(
        wf: Workflow,
        prefix: str,
        bindings: dict,
        inherited: Resources,
        parent: str | None,
        entry_deps: list[str],
        guard: list | None,
        trigger: str,
        finalizer: bool,
        directory_override=None,
    ) -> str:
        if id(wf) in visiting:
            raise ValueError("Recursive workflow definition")
        visiting.add(id(wf))
        if set(bindings) != set(wf.inputs):
            raise ValueError(f"{prefix}: expected inputs {wf.inputs}, got {list(bindings)}")
        if wf.max_active is not None and wf.max_active < 1:
            raise ValueError("max_active must be positive")
        ids = [n.id for n in wf.nodes]
        if len(ids) != len(set(ids)) or any(not NAME.fullmatch(n) for n in ids):
            raise ValueError(f"{prefix}: duplicate or invalid invocation identity")
        resolved = wf.resources or inherited
        entry = prefix + "/@enter"
        exit_id = prefix + "/@exit"
        targets = {
            n.id: prefix + "/" + n.id + ("/@exit" if isinstance(n.definition, Workflow) else "")
            for n in wf.nodes
        }

        def bind(value: Any) -> Any:
            if isinstance(value, Ref):
                if value.node not in targets:
                    raise ValueError(f"{prefix}: unknown reference {value.node}")
                return {"$ref": targets[value.node], "output": value.output}
            if isinstance(value, Input):
                if value.name not in bindings:
                    raise ValueError(f"Unknown workflow input {value.name}")
                return {"$ref": entry, "output": value.name}
            if isinstance(value, Select):
                return {"$select": [bind(ref) for ref in value.refs], "default": bind(value.default)}
            if isinstance(value, dict):
                if any(not isinstance(k, str) or k.startswith("$") for k in value):
                    raise ValueError("Input dictionary keys must be strings and cannot start with $")
                return {k: bind(v) for k, v in value.items()}
            if isinstance(value, (tuple, list)):
                return [bind(v) for v in value]
            if isinstance(value, Path):
                return str(value)
            dumps(value)
            return value

        nodes[entry] = dict(
            kind="enter",
            scope=parent,
            deps=entry_deps,
            inputs=bindings,
            when=guard,
            trigger=trigger,
            finalizer=finalizer,
            outputs=list(wf.inputs),
        )
        scopes[prefix] = dict(
            parent=parent, entry=entry, exit=exit_id, max_active=wf.max_active, outputs=bind(wf.outputs)
        )
        scopes[prefix]["directory"] = directory_spec(
            directory_override if directory_override is not None else wf.directory
        )
        for invocation in wf.nodes:
            node_id = prefix + "/" + invocation.id
            effective = dict(invocation.inputs)
            if isinstance(invocation.definition, Task):
                task_definition = invocation.definition
                if not isinstance(task_definition.inputs, list) or not all(
                    isinstance(p, TaskInput) for p in task_definition.inputs
                ):
                    raise ValueError("Task.inputs must be a list of TaskInput objects")
                if not isinstance(task_definition.outputs, list) or not all(
                    isinstance(p, TaskOutput) for p in task_definition.outputs
                ):
                    raise ValueError("Task.outputs must be a list of TaskOutput objects")
                for port in task_definition.inputs:
                    if port.name not in effective:
                        if port.source is not None:
                            effective[port.name] = port.source
                        elif not port.essential:
                            effective[port.name] = port.default
                    if port.kind != "value" and isinstance(effective.get(port.name), (str, Path)):
                        effective[port.name] = str(Path(effective[port.name]).expanduser().resolve())
            args = bind(effective)
            try:
                deps = [targets[n] for n in invocation.after]
                when = [targets[invocation.when[0]], invocation.when[1]] if invocation.when else None
            except KeyError as exc:
                raise ValueError(f"{node_id}: unknown dependency {exc}") from exc
            deps += [r["$ref"] for r in references(args)]
            if when:
                deps.append(when[0])
            deps = sorted(set([entry, *deps]))
            resource = invocation.resources or resolved
            if isinstance(invocation.definition, Workflow):
                if invocation.finalizer:
                    raise ValueError("First-pass finalizers must be executable tasks")
                expand(
                    invocation.definition,
                    node_id,
                    args,
                    resource,
                    prefix,
                    deps,
                    when,
                    invocation.trigger,
                    invocation.finalizer,
                    invocation.directory,
                )
                continue
            definition = invocation.definition
            task = asdict(definition) if isinstance(definition, Task) else None
            if task and isinstance(definition, Task):
                if task["script"] is not None:
                    from .scripts import capture

                    task["script"] = capture(task["script"])
                for port in task["inputs"]:
                    port["source"] = args.get(port["name"])
                task["resources"] = asdict(invocation.resources or definition.resources or resolved)
                task["directory"] = directory_spec(
                    invocation.directory if invocation.directory is not None else definition.directory
                )
            nodes[node_id] = dict(
                kind="task" if task else "decision",
                scope=prefix,
                deps=deps,
                inputs=args,
                when=when,
                trigger=invocation.trigger,
                finalizer=invocation.finalizer,
                task=task,
                policy=definition if not task else None,
                outputs=[port["name"] for port in task["outputs"]] if task else ["branch"],
            )
        children = sorted(targets.values())
        nodes[exit_id] = dict(
            kind="exit",
            scope=prefix,
            deps=sorted(set([entry, *children])),
            inputs={},
            when=None,
            trigger="all_terminal",
            finalizer=True,
            outputs=list(wf.outputs),
        )
        visiting.remove(id(wf))
        return exit_id

    if not NAME.fullmatch(flow.name):
        raise ValueError("Invalid workflow name")
    root = expand(flow, flow.name, inputs or {}, Resources(), None, [], None, "all_success", False)
    spec = json.loads(dumps(dict(schema_version=VERSION, nodes=nodes, scopes=scopes, root=root)))
    validate(spec)
    return spec


def required_references(value: Any) -> list[dict]:
    if isinstance(value, dict):
        if "$ref" in value:
            return [value]
        if "$select" in value:
            return required_references(value["default"])
        return [ref for item in value.values() for ref in required_references(item)]
    if isinstance(value, list):
        return [ref for item in value for ref in required_references(item)]
    return []


def guard_set(node: dict, spec: dict) -> set[tuple[str, str]]:
    guards = {tuple(node["when"])} if node["when"] else set()
    scope = node["scope"]
    while scope:
        definition = spec["scopes"][scope]
        entry = spec["nodes"][definition["entry"]]
        if entry["when"]:
            guards.add(tuple(entry["when"]))
        scope = definition["parent"]
    return guards


def validate(spec: dict) -> None:
    if spec.get("schema_version") not in (3, VERSION):
        raise ValueError("Unsupported workflow schema version")
    directories = [scope.get("directory") for scope in spec["scopes"].values()]
    directories += [
        node["task"].get("directory") for node in spec["nodes"].values() if node["kind"] == "task"
    ]
    for directory in directories:
        validate_directory(directory)
    if spec["schema_version"] == 3 and any(directory is not None for directory in directories):
        raise ValueError("Directory configuration requires workflow schema version 4")
    nodes = spec["nodes"]
    pending = set(nodes)
    seen: set[str] = set()
    for node_id, node in nodes.items():
        if node["trigger"] not in ("all_success", "all_terminal", "selected_success"):
            raise ValueError(f"{node_id}: invalid trigger")
        if node["finalizer"] and node["kind"] == "task" and node["trigger"] != "all_terminal":
            raise ValueError("Finalizers require trigger='all_terminal'")
        for dep in node["deps"]:
            if dep not in nodes:
                raise ValueError(f"{node_id}: unknown dependency {dep}")
        for ref in references(node["inputs"]):
            if ref["$ref"] not in nodes or ref["output"] not in nodes[ref["$ref"]]["outputs"]:
                raise ValueError(f"{node_id}: invalid output reference {ref}")
        if node["when"] and nodes[node["when"][0]]["kind"] != "decision":
            raise ValueError("Branch guards must reference a decision")
        if node["kind"] == "decision":
            _import_ref(node["policy"])
        if node["kind"] == "task":
            task = node["task"]
            if sum(bool(task.get(key)) for key in ("python", "command", "script")) != 1:
                raise ValueError("Task needs exactly one of python, command, or script")
            if task.get("script"):
                from .scripts import validate_script

                validate_script(task["script"])
            if any(
                not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
                or not isinstance(value, str)
                or "\x00" in value
                for key, value in task["env"].items()
            ):
                raise ValueError("Environment requires valid variable names and string values")
            if task["python"]:
                _import_ref(task["python"])
            if task["command"] and (
                isinstance(task["command"], str) or not all(isinstance(x, str) for x in task["command"])
            ):
                raise ValueError("Commands must be argument lists")
            validate_ports(task)
            if set(node["inputs"]) != {p["name"] for p in task["inputs"]}:
                raise ValueError(f"{node_id}: task input contract mismatch")
            resource = task["resources"]
            if resource["cpus"] < 1 or (
                resource["timeout_seconds"] is not None and resource["timeout_seconds"] <= 0
            ):
                raise ValueError("Invalid resource request")
            retry = task["retry"]
            if retry["max_attempts"] < 1 or retry["backoff_seconds"] < 0:
                raise ValueError("Invalid retry policy")
            if set(retry["categories"]) - {"application", "timeout"}:
                raise ValueError("Unsupported retry category")
    for node_id, node in nodes.items():
        own_guards = guard_set(node, spec)
        for ref in required_references(node["inputs"]):
            if not guard_set(nodes[ref["$ref"]], spec) <= own_guards:
                raise ValueError(f"{node_id}: conditional output needs Select or a matching branch guard")
        if node["trigger"] == "all_success":
            for ref in references(node["inputs"]):
                if not guard_set(nodes[ref["$ref"]], spec) <= own_guards:
                    raise ValueError(f"{node_id}: conditional join needs selected_success or all_terminal")
    for scope in spec["scopes"].values():
        own_guards = guard_set(nodes[scope["exit"]], spec)
        for ref in required_references(scope["outputs"]):
            if ref["$ref"] in nodes and not guard_set(nodes[ref["$ref"]], spec) <= own_guards:
                raise ValueError("Conditional workflow outputs need Select")
        for ref in references(scope["outputs"]):
            if ref["$ref"] not in nodes or ref["output"] not in nodes[ref["$ref"]]["outputs"]:
                raise ValueError(f"Invalid workflow output {ref}")
    while pending:
        ready = {n for n in pending if set(nodes[n]["deps"]) <= seen}
        if not ready:
            raise ValueError("Dependency cycle detected")
        seen |= ready
        pending -= ready
    dumps(spec)


def _import_ref(value: str) -> None:
    if not isinstance(value, str) or ":" not in value or "<locals>" in value:
        raise ValueError("Callables must use importable 'module:function' references")


def validate_ports(task: dict) -> None:
    destinations: list[Path] = []
    for direction in ("inputs", "outputs"):
        ports = task[direction]
        names = [port["name"] for port in ports]
        if len(names) != len(set(names)) or any(
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", n) for n in names
        ):
            raise ValueError(f"Duplicate or invalid {direction} names")
        for port in ports:
            if port["kind"] not in {"value", "file", "directory"}:
                raise ValueError("Port kind must be value, file, or directory")
            if port["mode"] not in {"copy", "link"}:
                raise ValueError("Transfer mode must be copy or link")
            if not isinstance(port["essential"], bool):
                raise ValueError("essential must be a Boolean")
            if port["kind"] == "value" and (port["destination"] is not None or port["mode"] != "copy"):
                raise ValueError("Value ports do not have destinations or transfer modes")
            if direction == "outputs":
                if port["kind"] == "value" and port["source"] is not None:
                    raise ValueError("Value outputs are returned by name, not collected from a source path")
                if port["source"] is not None:
                    relative_path(port["source"])
                if port["validator"] is not None:
                    _import_ref(port["validator"])
            elif port["kind"] != "value" and port["default"] is not None:
                raise ValueError("Optional artifact inputs use None as their default")
            destination = port["destination"]
            if direction == "inputs" and port["kind"] != "value" and destination is None:
                destination = "inputs/" + port["name"]
            if destination is not None:
                if not isinstance(destination, str) or not destination:
                    raise ValueError("Destination must be a nonempty path string")
                path = Path(destination)
                if direction == "inputs" or not path.is_absolute():
                    relative_path(destination)
                if any(
                    path == other or path in other.parents or other in path.parents for other in destinations
                ):
                    raise ValueError("Input/output transfer destinations must not overlap")
                destinations.append(path)


def relative_path(value: str) -> None:
    reserved = {
        "input.json",
        "staged-inputs.json",
        "outputs.json",
        "result.json",
        "runner.json",
        "stdout.log",
        "stderr.log",
        "job.sh",
        "job.zsh",
        "job.fish",
        "source.script",
        "script.json",
    }
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("Expected a nonempty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path == Path(".") or path.parts[0] in reserved:
        raise ValueError(f"Path must stay in the attempt and avoid runner-owned files: {value}")
