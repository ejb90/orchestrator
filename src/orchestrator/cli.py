"""CLI loading Python or static YAML definitions and inspecting durable runs."""

from __future__ import annotations

import json
import runpy
import sys
from functools import wraps
from pathlib import Path

import rich_click as click
from click.exceptions import Exit
from rich.console import Console
from rich.table import Table
from rich.text import Text

from .api import Workflow
from .engine import Coordinator, create_run
from .spec import compile_workflow
from .storage import Store


def errors(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exit:
            raise
        except (ValueError, OSError, RuntimeError, KeyError) as exc:
            raise click.ClickException(str(exc)) from exc

    return wrapped


def load(path: str, inputs: str) -> dict:
    source = Path(path).resolve()
    sys.path.insert(0, str(source.parent))
    values = json.loads(inputs)
    if not isinstance(values, dict):
        raise ValueError("Workflow inputs must be a JSON object")
    if source.suffix.lower() in {".yaml", ".yml"}:
        from .yaml_loader import load_yaml

        return load_yaml(source, values)
    namespace = runpy.run_path(str(source))
    workflow = namespace.get("workflow")
    if not isinstance(workflow, Workflow):
        raise ValueError("Definition file must export a Workflow named 'workflow'")
    return compile_workflow(workflow, values)


def definition_options(function):
    function = click.option("--inputs", default="{}", help="Workflow inputs as a JSON object.")(function)
    return click.argument(
        "workflow_file",
        type=click.Path(exists=True, dir_okay=False),
        help="Python (.py) or YAML (.yaml/.yml) definition.",
    )(function)


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    epilog="Start here: orchestrator plan examples/sensor_report.yaml --tree. Use --root PATH before the command.",
)
@click.rich_config(
    help_config={
        "style_option": "bold cyan",
        "style_switch": "bold magenta",
        "style_argument": "bold yellow",
        "style_command": "bold cyan",
        "style_options_panel_border": "blue",
        "style_commands_panel_border": "blue",
        "show_arguments": True,
        "errors_panel_title": "Error:",
    }
)
@click.command_panel("Workflow definitions", commands=["validate", "plan", "render"])
@click.command_panel("Execution", commands=["run", "resume", "cancel"])
@click.command_panel("Inspect runs", commands=["status", "inspect", "logs", "script", "events"])
@click.option(
    "--root",
    type=click.Path(path_type=Path),
    default=".orchestrator",
    show_default=True,
    help="Directory containing persisted runs; place this option before the command.",
    panel="Storage",
)
@click.pass_context
def main(ctx, root: Path):
    """Build, visualize, and run Python or YAML workflows.

    Local execution requires --allow-local. Slurm support is experimental.
    """
    ctx.obj = root.resolve()


@main.command("validate")
@definition_options
@errors
def validate_command(workflow_file, inputs):
    """Check a workflow's contracts and dependencies without running it."""
    spec = load(workflow_file, inputs)
    Console().print(Text(f"Valid: {len(spec['nodes'])} expanded nodes", style="green"))


@main.command("plan")
@definition_options
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["json", "table", "tree"]),
    default=None,
    show_default="json",
    help="Plan output: full JSON, dependency table, or prerequisite tree.",
    panel="Output",
)
@click.option(
    "--tree",
    "show_tree",
    is_flag=True,
    help="Show a Rich prerequisite tree (shorthand for --format tree).",
    panel="Output",
)
@errors
def plan_command(workflow_file, inputs, output_format, show_tree):
    """Inspect the compiled workflow as JSON, a table, or a dependency tree without submission."""
    if show_tree and output_format is not None:
        raise click.UsageError("Use either --tree or --format, not both")
    output_format = "tree" if show_tree else output_format or "json"
    spec = load(workflow_file, inputs)
    if output_format == "json":
        click.echo(json.dumps(spec, indent=2, sort_keys=True))
    else:
        from .presentation import plan_table, plan_tree

        Console().print(plan_tree(spec) if output_format == "tree" else plan_table(spec))


def display(state: dict, json_output: bool):
    if json_output:
        click.echo(json.dumps(state, indent=2, sort_keys=True))
        return
    console = Console()
    styles = {
        "succeeded": "bold green",
        "failed": "bold red",
        "cancelled": "yellow",
        "uncertain": "bold magenta",
        "skipped": "dim",
        "active": "cyan",
        "running": "cyan",
        "waiting": "yellow",
    }
    title = Text(f"Run {state['run_id']}: ")
    title.append(state["status"], style=styles.get(state["status"], ""))
    console.print(title)
    table = Table("Node", "State", "Attempts", "Reason", header_style="bold cyan", border_style="blue")
    for column in table.columns:
        column.overflow = "fold"
    for name, node in state["nodes"].items():
        table.add_row(
            Text(name, style="cyan"),
            Text(node["status"], style=styles.get(node["status"], "")),
            str(len(node["attempts"])),
            Text(node.get("reason") or ""),
        )
    console.print(table)
    if "outputs" in state:
        console.print_json(data=state["outputs"])


def directory(root: Path, run_id: str) -> Path:
    if len(run_id) != 32 or any(c not in "0123456789abcdef" for c in run_id):
        raise ValueError("Run ID must be a 32-character hexadecimal identifier")
    path = root / run_id
    if not (path / "state.sqlite").is_file():
        raise ValueError(f"Unknown run {run_id}")
    return path


def read_state(path: Path) -> dict:
    store = Store(path / "state.sqlite")
    try:
        return store.load()
    finally:
        store.close()


@main.command("run")
@definition_options
@click.option("--allow-local", is_flag=True, help="Explicitly enable compute on this host.")
@click.option(
    "--max-active",
    default=4,
    type=click.IntRange(min=1),
    show_default=True,
    help="Maximum number of active task attempts.",
)
@click.option("--json", "json_output", is_flag=True, help="Write machine-readable JSON to stdout.")
@click.pass_obj
@errors
def run_command(root, workflow_file, inputs, allow_local, max_active, json_output):
    """Create a run from a Python or YAML definition and execute eligible tasks."""
    path = create_run(load(workflow_file, inputs), root, allow_local=allow_local, max_active=max_active)
    click.echo(f"Run ID: {path.name}", err=True)
    state = Coordinator(path).run()
    display(state, json_output)
    if state["status"] != "succeeded":
        raise Exit(2 if state["status"] == "uncertain" else 1)


@main.command("resume")
@click.argument("run_id", help="Run ID printed when the run was created.")
@click.option("--json", "json_output", is_flag=True, help="Write machine-readable JSON to stdout.")
@click.pass_obj
@errors
def resume_command(root, run_id, json_output):
    """Reconcile existing attempts before scheduling additional work."""
    state = Coordinator(directory(root, run_id)).run()
    display(state, json_output)
    if state["status"] != "succeeded":
        raise Exit(2 if state["status"] == "uncertain" else 1)


@main.command("status")
@click.argument("run_id", help="Run ID printed when the run was created.")
@click.option("--json", "json_output", is_flag=True, help="Write machine-readable JSON to stdout.")
@click.pass_obj
@errors
def status_command(root, run_id, json_output):
    """Show the latest persisted run state without taking ownership."""
    display(read_state(directory(root, run_id)), json_output)


@main.command("inspect")
@click.argument("run_id", help="Run ID printed when the run was created.")
@click.argument("node_id", help="Full hierarchical node ID, shown by status or plan.")
@click.pass_obj
@errors
def inspect_command(root, run_id, node_id):
    """Print a node's state and complete attempt history as JSON."""
    click.echo(json.dumps(read_state(directory(root, run_id))["nodes"][node_id], indent=2))


@main.command("logs")
@click.argument("run_id", help="Run ID printed when the run was created.")
@click.argument("node_id", help="Full hierarchical node ID, shown by status or plan.")
@click.option("--attempt", type=click.IntRange(min=1), help="1-based attempt; defaults to latest.")
@click.option("--stderr", is_flag=True, help="Read stderr instead of stdout.")
@click.pass_obj
@errors
def logs_command(root, run_id, node_id, attempt, stderr):
    """Print task logs for the latest or a selected attempt."""
    path = directory(root, run_id)
    attempts = read_state(path)["nodes"][node_id]["attempts"]
    if not attempts or (attempt is not None and attempt > len(attempts)):
        raise ValueError("No such attempt")
    selected = attempts[attempt - 1] if attempt else attempts[-1]
    log = path / selected["path"] / ("stderr.log" if stderr else "stdout.log")
    click.echo(log.read_text(errors="replace") if log.exists() else "", nl=False)


@main.command("events")
@click.argument("run_id", help="Run ID printed when the run was created.")
@click.option("--json", "json_output", is_flag=True, help="Write machine-readable JSON to stdout.")
@click.pass_obj
@errors
def events_command(root, run_id, json_output):
    """Show the ordered orchestration event history."""
    store = Store(directory(root, run_id) / "state.sqlite")
    try:
        events = store.events()
    finally:
        store.close()
    if json_output:
        click.echo(json.dumps(events, indent=2))
    else:
        for event in events:
            click.echo(f"{event['revision']:5} {event['kind']} {json.dumps(event['data'])}")


@main.command("cancel")
@click.argument("run_id", help="Run ID printed when the run was created.")
@click.pass_obj
@errors
def cancel_command(root, run_id):
    """Request cancellation; the coordinator reconciles the actual outcome."""
    path = directory(root, run_id)
    if read_state(path)["status"] in {"succeeded", "failed", "cancelled", "skipped"}:
        click.echo("Run is already terminal")
        return
    (path / "cancel.request").touch()
    click.echo("Cancellation requested. The active coordinator will reconcile it; otherwise run resume.")


@main.command("script")
@click.argument("run_id", help="Run ID printed when the run was created.")
@click.argument("node_id", help="Full hierarchical node ID, shown by status or plan.")
@click.option("--attempt", type=click.IntRange(min=1), help="1-based attempt; defaults to latest.")
@click.pass_obj
@errors
def script_command(root, run_id, node_id, attempt):
    """Print the exact retained script after verifying its digest."""
    from .scripts import verified_script

    path = directory(root, run_id)
    attempts = read_state(path)["nodes"][node_id]["attempts"]
    if not attempts or (attempt is not None and attempt > len(attempts)):
        raise ValueError("No such attempt")
    selected = attempts[attempt - 1] if attempt else attempts[-1]
    attempt_path = path / selected["path"]
    record = verified_script(attempt_path)
    if record != selected.get("script"):
        raise ValueError("Script metadata differs from the durable intent")
    click.echo((attempt_path / record["path"]).read_text(), nl=False)


@main.command("render")
@definition_options
@click.option(
    "--output", required=True, type=click.Path(path_type=Path), help="New directory for script previews."
)
@errors
def render_command(workflow_file, inputs, output):
    """Render previews without execution. Paths and IDs belong to the preview only."""
    from .scripts import render
    from .spec import digest

    spec = load(workflow_file, inputs)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    for node_id, node in spec["nodes"].items():
        if node["kind"] != "task" or not node["task"].get("script"):
            continue
        target = output / node_id
        target.mkdir(parents=True)
        render(
            target,
            dict(
                run_id="preview",
                node_id=node_id,
                attempt_id="preview",
                spec_digest=digest(spec),
                task=node["task"],
                inputs=node["inputs"],
            ),
        )
    click.echo(f"Previews written to {output}; create a run to obtain executable attempt manifests.")
