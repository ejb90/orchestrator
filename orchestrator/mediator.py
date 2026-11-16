"""Default mediation behavior for generated workflow scripts."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path


def mediate(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mediate workflow task completion")
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--status", required=True, type=int)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument(
        "--on-failure",
        choices=("ask", "stop", "continue"),
        default="ask",
        help="Decision policy when a task fails.",
    )
    args = parser.parse_args(argv)

    args.state_dir.mkdir(parents=True, exist_ok=True)
    event = {
        "workflow": args.workflow,
        "task": args.task,
        "status": args.status,
        "time": datetime.now(UTC).isoformat(),
    }
    with (args.state_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")

    if args.status == 0:
        return 0

    if args.on_failure == "continue":
        return 0

    if args.on_failure == "stop" or not sys.stdin.isatty():
        _write_stop(args.state_dir, args.workflow, args.task, args.status)
        return args.status

    answer = input(
        f"Task {args.task!r} in workflow {args.workflow!r} failed "
        f"with status {args.status}. Continue? [y/N] "
    ).strip()
    if answer.lower() in {"y", "yes"}:
        return 0

    _write_stop(args.state_dir, args.workflow, args.task, args.status)
    return args.status


def _write_stop(state_dir: Path, workflow: str, task: str, status: int) -> None:
    (state_dir / "stop").write_text(
        f"workflow={workflow}\ntask={task}\nstatus={status}\n",
        encoding="utf-8",
    )
