"""Data model for workflows and tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Self


class ExecutionMode(StrEnum):
    """How a workflow should submit its immediate children."""

    SERIAL = "serial"
    PARALLEL = "parallel"


@dataclass(frozen=True)
class Task:
    """A single runnable unit or a nested workflow."""

    name: str
    command: str | None = None
    workflow: "Workflow | None" = None
    slurm: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("task name cannot be empty")
        if (self.command is None) == (self.workflow is None):
            raise ValueError(
                f"task {self.name!r} must define exactly one of command or workflow"
            )

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> Self:
        workflow_data = data.get("workflow")
        workflow = Workflow.from_mapping(workflow_data) if workflow_data else None
        command = data.get("command")
        slurm = data.get("slurm", {})
        if slurm is None:
            slurm = {}
        if not isinstance(slurm, dict):
            raise ValueError(f"task {data.get('name')!r} slurm options must be a mapping")
        return cls(
            name=str(data["name"]),
            command=str(command) if command is not None else None,
            workflow=workflow,
            slurm={str(key): str(value) for key, value in slurm.items()},
        )


@dataclass(frozen=True)
class Workflow:
    """A workflow whose tasks are submitted serially or in parallel."""

    name: str
    tasks: tuple[Task, ...]
    mode: ExecutionMode = ExecutionMode.SERIAL

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("workflow name cannot be empty")
        if not self.tasks:
            raise ValueError(f"workflow {self.name!r} must contain at least one task")

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> Self:
        tasks = data.get("tasks", [])
        if not isinstance(tasks, list):
            raise ValueError(f"workflow {data.get('name')!r} tasks must be a list")
        return cls(
            name=str(data["name"]),
            mode=ExecutionMode(str(data.get("mode", ExecutionMode.SERIAL))),
            tasks=tuple(Task.from_mapping(task) for task in tasks),
        )

    @classmethod
    def from_json_file(cls, path: str | Path) -> Self:
        import json

        with Path(path).open(encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("workflow JSON root must be an object")
        return cls.from_mapping(data)
