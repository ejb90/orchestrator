"""Resolve execution bases while retaining durable per-attempt directory identities."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from .api import Directory


def directory_spec(value: Directory | str | None) -> dict | None:
    """Normalize an authoring value without creating directories or running commands."""
    if value is None:
        return None
    if isinstance(value, str):
        value = Directory(value)
    if not isinstance(value, Directory):
        raise ValueError("directory must be a path string or Directory")
    result = asdict(value)
    if result["relative_to"] is not None and not isinstance(result["relative_to"], str):
        raise ValueError("Directory relative_to must be text")
    if not isinstance(result["path"], str) or not result["path"] or "\x00" in result["path"]:
        raise ValueError("Directory path must be nonempty text without NUL bytes")
    path = Path(result["path"]).expanduser()
    result["path"] = str(path)
    result["relative_to"] = result["relative_to"] or ("absolute" if path.is_absolute() else "parent")
    validate_directory(result)
    return result


def validate_directory(value: dict | None) -> None:
    """Validate a serialized base and its absolute/root/parent anchor."""
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != {"path", "relative_to"}:
        raise ValueError("Directory requires path and relative_to")
    path, anchor = value["path"], value["relative_to"]
    if not isinstance(path, str) or not path or "\x00" in path:
        raise ValueError("Directory path must be nonempty text without NUL bytes")
    if not isinstance(anchor, str) or anchor not in {"absolute", "root", "parent"}:
        raise ValueError("Directory relative_to must be absolute, root, or parent")
    if Path(path).is_absolute() != (anchor == "absolute"):
        raise ValueError(
            "Absolute directory paths require the absolute anchor; root/parent paths must be relative"
        )


def resolve_directories(spec: dict, root: Path) -> dict[str, str]:
    """Resolve every task base at run creation without creating task directories.

    Root is the individual run directory, including its run ID. Parent is the logical
    enclosing workflow's resolved base, never another task's live attempt directory.
    The returned mapping is persisted so retries cannot re-evaluate mutable settings.
    """
    root = root.resolve()

    def resolve(config: dict | None, parent: Path) -> Path:
        if config is None:
            return parent
        anchor = config["relative_to"]
        base = root if anchor == "root" else parent
        return (Path(config["path"]) if anchor == "absolute" else base / config["path"]).resolve()

    scopes: dict[str, Path] = {}
    pending = set(spec["scopes"])
    while pending:
        ready = sorted(
            name
            for name in pending
            if spec["scopes"][name]["parent"] is None or spec["scopes"][name]["parent"] in scopes
        )
        if not ready:
            raise ValueError("Invalid directory scope hierarchy")
        for name in ready:
            scope = spec["scopes"][name]
            scopes[name] = resolve(scope.get("directory"), scopes.get(scope["parent"], root))
        pending.difference_update(ready)
    return {
        name: str(resolve(node["task"].get("directory"), scopes[node["scope"]]))
        for name, node in spec["nodes"].items()
        if node["kind"] == "task"
    }


def attempt_directory(base: Path, root: Path, node_id: str, attempt_id: str) -> Path:
    """Locate a fresh isolated attempt, adding a run namespace to external bases.

    Bases inside the run root use BASE/attempts/NODE/ATTEMPT. External bases use
    BASE/RUN_ID/attempts/NODE/ATTEMPT to separate runs sharing the same scratch base.
    """
    if not base.is_relative_to(root):
        base = base / root.name
    return base / "attempts" / node_id / attempt_id
