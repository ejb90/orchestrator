import json
import os
import subprocess
import sys

from click.testing import CliRunner

from orchestrator.cli import main
from orchestrator.spec import VERSION


def test_help_groups_arguments_and_short_flag():
    runner = CliRunner()
    help_result = runner.invoke(main, ["--help"])
    assert help_result.exit_code == 0
    for panel in ("Workflow definitions", "Execution", "Inspect runs", "Storage"):
        assert panel in help_result.output
    for name in main.commands:
        assert name in help_result.output
        command = runner.invoke(main, [name, "-h"])
        assert command.exit_code == 0, command.output
        assert "Show this message" in command.output
    plan = runner.invoke(main, ["plan", "--help"])
    assert "WORKFLOW_FILE" in plan.output and "YAML" in plan.output
    assert "Output" in plan.output and "--tree" in plan.output


def test_help_colour_and_no_colour():
    # Rich Click reads colour configuration at import; test fresh CLI processes.
    env = dict(os.environ)
    for name in ("NO_COLOR", "FORCE_COLOR", "PY_COLORS", "GITHUB_ACTIONS", "RICH_CLICK_THEME"):
        env.pop(name, None)
    env["TERM"] = "xterm-256color"
    command = [sys.executable, "-m", "orchestrator", "--help"]
    colourful = subprocess.run(command, env={**env, "FORCE_COLOR": "1"}, capture_output=True, text=True)
    assert colourful.returncode == 0 and "\x1b[" in colourful.stdout
    plain = subprocess.run(command, env={**env, "NO_COLOR": "1"}, capture_output=True, text=True)
    assert plain.returncode == 0 and "\x1b[" not in plain.stdout
    assert "Workflow definitions" in plain.stdout


def test_forced_colour_does_not_change_json_or_exit_codes(tmp_path):
    runner = CliRunner()
    env = {"FORCE_COLOR": "1"}
    plan = runner.invoke(main, ["plan", "examples/diamond.py"], color=True, env=env)
    assert plan.exit_code == 0 and "\x1b[" not in plan.stdout
    assert json.loads(plan.stdout)["schema_version"] == VERSION
    root_args = ["--root", str(tmp_path / "runs")]
    run = runner.invoke(main, [*root_args, "run", "examples/diamond.py", "--allow-local", "--json"], env=env)
    assert run.exit_code == 0, run.output
    assert "\x1b[" not in run.stdout
    state = json.loads(run.stdout)
    assert state["outputs"] == {"value": 12}
    for args in (
        ["status", state["run_id"], "--json"],
        ["resume", state["run_id"], "--json"],
        ["inspect", state["run_id"], "diamond/left"],
        ["events", state["run_id"], "--json"],
    ):
        result = runner.invoke(main, [*root_args, *args], env=env)
        assert result.exit_code == 0 and "\x1b[" not in result.stdout
        json.loads(result.stdout)
    invalid = runner.invoke(main, ["plan", "examples/diamond.py", "--tree", "--format", "json"])
    assert invalid.exit_code == 2
    assert "Use either" in invalid.output
