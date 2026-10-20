"""Static YAML authoring translated into the existing Python builders."""

from __future__ import annotations

import json
import types
from dataclasses import fields
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

import yaml  # type: ignore[import-untyped]

from .api import (
    Directory,
    Handle,
    Input,
    Ref,
    Resources,
    Retry,
    Script,
    Task,
    TaskInput,
    TaskOutput,
    Workflow,
)
from .spec import compile_workflow


class DefinitionLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise ValueError("YAML aliases are not supported; use named task definitions")
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise ValueError("YAML mapping keys must be strings (quote ambiguous names)")
            if key in result:
                raise ValueError(f"Duplicate YAML key: {key}")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def mapping(value: Any, context: str, allowed: set[str] | None = None) -> dict:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{context}: expected a mapping with string keys")
    if allowed is not None and (unknown := set(value) - allowed):
        raise ValueError(f"{context}: unknown fields {sorted(unknown)}")
    return value


def matches(value: Any, annotation: Any) -> bool:
    if annotation is Any:
        return True
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is types.UnionType:
        return any(matches(value, option) for option in args)
    if origin in (list, tuple):
        return isinstance(value, origin) and all(matches(item, args[0]) for item in value)
    if origin is dict:
        return isinstance(value, dict) and all(
            matches(key, args[0]) and matches(item, args[1]) for key, item in value.items()
        )
    if annotation is float:
        return type(value) in (int, float)
    if annotation in (int, bool):
        return type(value) is annotation
    return isinstance(value, annotation)


def object_from(cls, value: Any, context: str):
    data = mapping(value, context, {field.name for field in fields(cls)})
    hints = get_type_hints(cls)
    for key, item in data.items():
        if not matches(item, hints[key]):
            raise ValueError(f"{context}.{key}: invalid type")
    try:
        return cls(**data)
    except TypeError as exc:
        raise ValueError(f"{context}: {exc}") from exc


def binding(value: Any) -> Any:
    if isinstance(value, dict):
        if "$ref" in value:
            target = value["$ref"]
            if set(value) != {"$ref"} or not isinstance(target, str) or target.count(".") != 1:
                raise ValueError("Output bindings require {$ref: node.output}")
            return Ref(*target.split("."))
        if "$input" in value:
            if set(value) != {"$input"} or not isinstance(value["$input"], str):
                raise ValueError("Workflow input bindings require {$input: name}")
            return Input(value["$input"])
        return {key: binding(item) for key, item in value.items()}
    if isinstance(value, list):
        return [binding(item) for item in value]
    return value


def absolute_source(value: Any, base: Path) -> Any:
    if isinstance(value, str):
        path = Path(value).expanduser()
        return str((base / path).resolve())
    return value


def binding_map(value: Any, context: str) -> dict:
    data = mapping(value, context)
    if any(key.startswith("$") for key in data):
        raise ValueError(f"{context}: binding names cannot start with $")
    return {key: binding(item) for key, item in data.items()}


def build_task(value: Any, base: Path, context: str) -> Task:
    data = dict(mapping(value, context))
    if isinstance(data.get("directory"), dict):
        data["directory"] = object_from(Directory, data["directory"], f"{context}.directory")
    for name, cls in (("resources", Resources), ("retry", Retry), ("script", Script)):
        if name in data:
            config = dict(mapping(data[name], f"{context}.{name}"))
            if name == "script" and "path" in config:
                config["path"] = absolute_source(config["path"], base)
            if name == "retry" and isinstance(config.get("categories"), list):
                config["categories"] = tuple(config["categories"])
            data[name] = object_from(cls, config, f"{context}.{name}")
    if isinstance(data.get("command"), list):
        data["command"] = tuple(data["command"])
    for direction, port_cls in (("inputs", TaskInput), ("outputs", TaskOutput)):
        if direction not in data:
            continue
        if not isinstance(data[direction], list):
            raise ValueError(f"{context}.{direction}: expected a list of port mappings")
        ports = []
        for index, port in enumerate(data[direction]):
            config = dict(mapping(port, f"{context}.{direction}[{index}]"))
            if direction == "inputs" and "source" in config:
                config["source"] = binding(config["source"])
                if config.get("kind", "value") != "value":
                    config["source"] = absolute_source(config["source"], base)
            ports.append(object_from(port_cls, config, f"{context}.{direction}[{index}]"))
        data[direction] = ports
    return object_from(Task, data, context)


def load_yaml(path: str | Path, inputs: dict | None = None) -> dict:
    """Compile a version-1 static YAML definition into the current workflow spec.

    Args:
        path: YAML definition file; literal script/artifact input paths resolve relative to it.
        inputs: Values for declared workflow inputs; these use common compiler semantics.

    Returns:
        The same schema-versioned specification produced by compile_workflow.

    Raises:
        ValueError: YAML syntax, format version, field types, or workflow validation fails.
        OSError: The definition or a referenced script source cannot be read.
    """
    source = Path(path).resolve()
    try:
        document = yaml.load(source.read_text(encoding="utf-8"), Loader=DefinitionLoader)
    except (yaml.YAMLError, ValueError) as exc:
        raise ValueError(f"{source}: {exc}") from exc
    document = mapping(
        document,
        "workflow",
        {"version", "name", "inputs", "resources", "max_active", "tasks", "nodes", "outputs", "directory"},
    )
    if type(document.get("version")) is not int or document["version"] != 1:
        raise ValueError("YAML definition requires version: 1")
    # Reject YAML-specific values (dates, sets, binary) before builder conversion.
    try:
        json.dumps(document, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("YAML values must be finite JSON-compatible values; quote dates") from exc
    if not isinstance(document.get("name"), str):
        raise ValueError("workflow.name: expected a string")
    names = document.get("inputs", [])
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        raise ValueError("workflow.inputs: expected a list of names")
    if len(names) != len(set(names)):
        raise ValueError("workflow.inputs: duplicate names")
    if inputs is not None:
        mapping(inputs, "workflow input values")
    resources = (
        object_from(Resources, document["resources"], "workflow.resources")
        if "resources" in document
        else None
    )
    limit = document.get("max_active")
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("workflow.max_active: expected a positive integer")
    flow = Workflow(document["name"], inputs=tuple(names), resources=resources, max_active=limit)
    directory = document.get("directory")
    if isinstance(directory, dict):
        directory = object_from(Directory, directory, "workflow.directory")
    flow.directory = directory
    tasks = {
        name: build_task(value, source.parent, f"tasks.{name}")
        for name, value in mapping(document.get("tasks", {}), "tasks").items()
    }
    for name, value in mapping(document.get("nodes"), "nodes").items():
        node = mapping(
            value,
            f"nodes.{name}",
            {"task", "inputs", "after", "resources", "trigger", "finalizer", "directory"},
        )
        task_name = node.get("task")
        if not isinstance(task_name, str) or task_name not in tasks:
            raise ValueError(f"nodes.{name}: unknown task {task_name!r}")
        task = tasks[task_name]
        args = dict(mapping(node.get("inputs", {}), f"nodes.{name}.inputs"))
        for port in task.inputs:
            if port.kind != "value" and port.name in args:
                args[port.name] = absolute_source(args[port.name], source.parent)
        after = node.get("after", [])
        if not isinstance(after, list) or any(not isinstance(item, str) for item in after):
            raise ValueError(f"nodes.{name}.after: expected a list of node names")
        if type(node.get("finalizer", False)) is not bool:
            raise ValueError(f"nodes.{name}.finalizer: expected a Boolean")
        if not isinstance(node.get("trigger", "all_success"), str):
            raise ValueError(f"nodes.{name}.trigger: expected a string")
        flow.add(
            name,
            task,
            inputs=binding_map(args, f"nodes.{name}.inputs"),
            after=tuple(Handle(item) for item in after),
            resources=object_from(Resources, node["resources"], f"nodes.{name}.resources")
            if "resources" in node
            else None,
            trigger=node.get("trigger", "all_success"),
            finalizer=node.get("finalizer", False),
            directory=object_from(Directory, node["directory"], f"nodes.{name}.directory")
            if isinstance(node.get("directory"), dict)
            else node.get("directory"),
        )
    flow.outputs = binding_map(document.get("outputs", {}), "workflow.outputs")
    return compile_workflow(flow, inputs)
