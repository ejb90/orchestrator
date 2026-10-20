"""Attempt-local staging and explicit, non-overwriting output collection."""

from __future__ import annotations

import importlib
import os
import shutil
from pathlib import Path
from typing import Any


def _path(value: Any) -> Path:
    return Path(value["path"] if isinstance(value, dict) else value)


def _present(path: Path) -> bool:
    try:
        path.stat()
        return True
    except FileNotFoundError:
        return False


def _exists(path: Path, kind: str) -> bool:
    return path.is_file() if kind == "file" else path.is_dir()


def _destination(value: str, directory: Path, *, external: bool = False) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = directory / path
        if not path.parent.resolve().is_relative_to(directory.resolve()):
            raise ValueError("Transfer destination escapes the attempt directory")
    elif not external:
        raise ValueError("Input destinations must be attempt-relative")
    if path == directory or (
        path.parent == directory
        and path.name
        in {
            "input.json",
            "staged-inputs.json",
            "outputs.json",
            "result.json",
            "runner.json",
            "stdout.log",
            "stderr.log",
        }
    ):
        raise ValueError("Transfer would overwrite runner-owned data")
    return path


def transfer(source: Path, destination: Path, mode: str) -> None:
    """Never overwrite an existing entry, including a dangling symbolic link."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir() and destination.parent.resolve().is_relative_to(source.resolve()):
        raise ValueError("Cannot collect a directory inside itself")
    if mode == "link":
        destination.symlink_to(source.resolve(), target_is_directory=source.is_dir())
    elif source.is_dir():
        # copytree refuses an existing directory; symlinks inside trees are preserved.
        shutil.copytree(source, destination, symlinks=True)
    else:
        with source.open("rb") as reader, destination.open("xb") as writer:
            shutil.copyfileobj(reader, writer)
            writer.flush()
            os.fsync(writer.fileno())
        shutil.copymode(source, destination)


def stage_inputs(values: dict, contract: list[dict], directory: Path) -> dict:
    staged = dict(values)
    for port in contract:
        if port["kind"] == "value":
            continue
        value = values[port["name"]]
        source = _path(value) if value is not None else None
        if source is None or not _present(source):
            if port["essential"]:
                raise ValueError(f"Missing essential input {port['name']}: {source}")
            staged[port["name"]] = None
            continue
        if not _exists(source, port["kind"]):
            raise ValueError(f"Input {port['name']} is not a {port['kind']}: {source}")
        destination = _destination(port["destination"] or "inputs/" + port["name"], directory)
        transfer(source, destination, port["mode"])
        staged[port["name"]] = {"path": str(destination), "kind": port["kind"]}
    return staged


def validate_outputs(values: dict, contract: list[dict], directory: Path) -> dict:
    """Read-only manifest validation; never collect files or run user code on resume."""
    if not isinstance(values, dict):
        raise ValueError("Task outputs must be a dictionary")
    known = {port["name"] for port in contract}
    required = {port["name"] for port in contract if port["essential"]}
    if set(values) - known or required - set(values):
        raise ValueError(
            f"Expected output keys {sorted(known)} (required: {sorted(required)}), got {sorted(values)}"
        )
    result = dict(values)
    for port in contract:
        name = port["name"]
        if name not in values or port["kind"] == "value":
            continue
        path = (directory / _path(values[name])).absolute()
        if not _exists(path, port["kind"]):
            raise ValueError(f"Missing {port['kind']} output {name}: {path}")
        result[name] = {"path": str(path), "kind": port["kind"]}
    # Import locally to avoid the compiler/contract module dependency cycle.
    from .spec import dumps

    dumps(result)
    return result


def collect_outputs(values: dict | None, contract: list[dict], directory: Path) -> dict:
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise ValueError("Task outputs must be a dictionary or None")
    candidates = dict(values)
    for port in contract:
        name = port["name"]
        if port["source"] is not None:
            if name in values:
                raise ValueError(f"Output {name} has both a declared source and a returned value")
            candidates[name] = port["source"]
        if port["kind"] != "value" and name in candidates:
            source = (directory / _path(candidates[name])) if candidates[name] is not None else None
            if source is None or not _present(source):
                if not port["essential"]:
                    del candidates[name]
                    continue
                raise ValueError(f"Missing essential output {name}: {source}")
    result = validate_outputs(candidates, contract, directory)
    # Validate the entire set before copying any outputs or publishing any success.
    for port in contract:
        name = port["name"]
        if name not in result or port["validator"] is None:
            continue
        module, name_path = port["validator"].split(":", 1)
        validator: Any = importlib.import_module(module)
        for attribute in name_path.split("."):
            validator = getattr(validator, attribute)
        if validator(result[name]) is not True:
            raise ValueError(f"Output validator rejected {name}: {port['validator']}")
    for port in contract:
        name = port["name"]
        if name in result and port["destination"] is not None:
            source = _path(result[name])
            destination = _destination(port["destination"], directory, external=True)
            transfer(source, destination, port["mode"])
            result[name] = {"path": str(destination), "kind": port["kind"]}
    return result
