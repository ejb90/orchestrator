"""Render workflows into bash or Slurm job command files."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from orchestrator.models import ExecutionMode, Task, Workflow


class RenderTarget(StrEnum):
    """Supported script backends."""

    BASH = "bash"
    SLURM = "slurm"


@dataclass(frozen=True)
class RenderedFile:
    """A script that should be written to disk."""

    relative_path: Path
    content: str


@dataclass(frozen=True)
class ScriptRenderer:
    """Build runnable scripts without building a DAG."""

    target: RenderTarget = RenderTarget.BASH
    mediator: str = "orchestrator mediate"

    def render(self, workflow: Workflow) -> list[RenderedFile]:
        files: list[RenderedFile] = []
        self._render_workflow(workflow, Path(), files)
        return files

    def write(self, workflow: Workflow, out_dir: str | Path) -> list[Path]:
        root = Path(out_dir)
        written: list[Path] = []
        for rendered in self.render(workflow):
            path = root / rendered.relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered.content, encoding="utf-8")
            path.chmod(0o755)
            written.append(path)
        return written

    def _render_workflow(
        self, workflow: Workflow, prefix: Path, files: list[RenderedFile]
    ) -> Path:
        workflow_dir = prefix / _slug(workflow.name)
        task_scripts: list[tuple[Task, Path]] = []

        for index, task in enumerate(workflow.tasks, start=1):
            task_prefix = workflow_dir / f"{index:02d}-{_slug(task.name)}"
            if task.workflow:
                task_path = self._render_workflow(task.workflow, task_prefix, files)
            else:
                task_path = task_prefix.with_suffix(".sh")
                files.append(
                    RenderedFile(
                        task_path,
                        self._task_script(workflow, task, task.command or ""),
                    )
                )
            task_scripts.append((task, task_path))

        workflow_script = workflow_dir / "run.sh"
        files.append(
            RenderedFile(
                workflow_script,
                self._workflow_script(workflow, workflow_dir, task_scripts),
            )
        )
        return workflow_script

    def _workflow_script(
        self, workflow: Workflow, workflow_dir: Path, task_scripts: list[tuple[Task, Path]]
    ) -> str:
        lines = [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            f"WORKFLOW_NAME={shlex.quote(workflow.name)}",
            'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
            'STATE_DIR="${STATE_DIR:-$SCRIPT_DIR/.orchestrator-state}"',
            'mkdir -p "$STATE_DIR"',
            "",
            'should_continue() { [[ ! -f "$STATE_DIR/stop" ]]; }',
            "",
        ]

        if workflow.mode is ExecutionMode.SERIAL:
            for task, script in task_scripts:
                rel_script = _relative_script(workflow_dir, script)
                submit = self._submit_command(task, rel_script)
                lines.extend(
                    [
                        "should_continue || exit 1",
                        submit,
                        "",
                    ]
                )
        else:
            pids: list[str] = []
            for index, (_task, script) in enumerate(task_scripts, start=1):
                pid_name = f"pid_{index}"
                pids.append(f"${pid_name}")
                rel_script = _relative_script(workflow_dir, script)
                submit = self._submit_command(_task, rel_script)
                lines.append(f"{submit} & {pid_name}=$!")
            lines.extend(
                [
                    "",
                    "status=0",
                    f"for pid in {' '.join(pids)}; do",
                    "  wait \"$pid\" || status=$?",
                    "done",
                    "exit \"$status\"",
                    "",
                ]
            )

        return "\n".join(lines)

    def _task_script(self, workflow: Workflow, task: Task, command: str) -> str:
        body = self._bash_task_script(workflow, task, command)
        if self.target is RenderTarget.BASH:
            return body
        return self._slurm_task_script(task, body)

    def _submit_command(self, task: Task, script: str) -> str:
        if self.target is RenderTarget.SLURM and task.workflow is None:
            return f'STATE_DIR="$STATE_DIR" sbatch --wait {script}'
        return f'STATE_DIR="$STATE_DIR" bash {script}'

    def _bash_task_script(self, workflow: Workflow, task: Task, command: str) -> str:
        mediator = self.mediator
        quoted_workflow = shlex.quote(workflow.name)
        quoted_task = shlex.quote(task.name)
        return "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -uo pipefail",
                "",
                'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
                'STATE_DIR="${STATE_DIR:-$SCRIPT_DIR/.orchestrator-state}"',
                'mkdir -p "$STATE_DIR"',
                "",
                "status=0",
                "{",
                command,
                "} || status=$?",
                "",
                f"{mediator} --workflow {quoted_workflow} --task {quoted_task} "
                '--status "$status" --state-dir "$STATE_DIR"',
                'mediate_status="$?"',
                'if [[ "$status" -ne 0 ]]; then',
                '  exit "$status"',
                "fi",
                'exit "$mediate_status"',
                "",
            ]
        )

    def _slurm_task_script(self, task: Task, body: str) -> str:
        directives = ["#SBATCH --job-name=" + _slug(task.name)]
        for key, value in task.slurm.items():
            option = key if key.startswith("--") else f"--{key}"
            directives.append(f"#SBATCH {option}={value}")
        lines = body.splitlines()
        return "\n".join([lines[0], *directives, *lines[1:], ""])


def _slug(value: str) -> str:
    keep = [char.lower() if char.isalnum() else "-" for char in value.strip()]
    slug = "-".join(part for part in "".join(keep).split("-") if part)
    return slug or "unnamed"


def _relative_script(workflow_dir: Path, script: Path) -> str:
    return '"$SCRIPT_DIR"/' + shlex.quote(str(script.relative_to(workflow_dir)))
