"""Command line interface for the orchestrator package."""

from __future__ import annotations

import argparse
from pathlib import Path

from orchestrator.mediator import mediate
from orchestrator.models import Workflow
from orchestrator.renderer import RenderTarget, ScriptRenderer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orchestrator")
    subcommands = parser.add_subparsers(dest="command", required=True)

    build = subcommands.add_parser("build", help="Build runnable workflow scripts")
    build.add_argument("workflow", type=Path, help="Workflow JSON file")
    build.add_argument("-o", "--out-dir", type=Path, default=Path("build/workflows"))
    build.add_argument(
        "--target",
        choices=[target.value for target in RenderTarget],
        default=RenderTarget.BASH.value,
    )
    build.add_argument(
        "--mediator",
        default="orchestrator mediate",
        help="Command appended to each task script after task execution.",
    )

    subcommands.add_parser("mediate", help="Run the default task completion mediator")

    args, remainder = parser.parse_known_args(argv)
    if args.command == "mediate":
        return mediate(remainder)

    workflow = Workflow.from_json_file(args.workflow)
    renderer = ScriptRenderer(
        target=RenderTarget(args.target),
        mediator=args.mediator,
    )
    written = renderer.write(workflow, args.out_dir)
    for path in written:
        print(path)
    return 0
