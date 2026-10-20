import io
import json

import pytest
from click.testing import CliRunner
from rich.console import Console

from orchestrator.cli import load, main
from orchestrator.presentation import plan_table, plan_tree


def render(spec):
    output = io.StringIO()
    Console(file=output, width=240, color_system=None).print(plan_table(spec))
    return output.getvalue()


def test_diamond_preserves_every_node_and_dependency():
    spec = load("examples/diamond.py", "{}")
    table = plan_table(spec)
    # Inspect cells rather than relying on terminal line wrapping.
    columns = [list(column.cells) for column in table.columns]
    names = [cell.plain for cell in columns[0]]
    dependencies = {name: columns[2][index].plain.splitlines() for index, name in enumerate(names)}
    assert set(names) == set(spec["nodes"])
    assert dependencies["diamond/join"] == ["diamond/@enter", "diamond/left", "diamond/right"]
    assert names.index("diamond/seed") < names.index("diamond/left") < names.index("diamond/join")
    assert names.index("diamond/seed") < names.index("diamond/right") < names.index("diamond/join")
    assert render(spec) == render(spec)


@pytest.mark.parametrize(
    "path,inputs,expected",
    [
        ("examples/nested.py", '{"start": 3}', ["nested/left/@enter", "nested/right/@exit", "max active: 1"]),
        ("examples/branches.py", "{}", ["when branches/choose = large", "trigger: selected_success"]),
        ("examples/convergence.py", "{}", ["required finalizer", "policy: examples.tasks:converge"]),
        ("examples/script.yaml", "{}", ["yaml_report/write", "bash / local"]),
    ],
)
def test_controls_nesting_and_yaml(path, inputs, expected):
    output = render(load(path, inputs))
    for item in expected:
        assert item in output


def test_cli_table_and_json_do_not_submit(tmp_path, monkeypatch):
    from pathlib import Path

    source = Path("examples/script.yaml").resolve()
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    table = runner.invoke(main, ["plan", str(source), "--format", "table"], terminal_width=240)
    assert table.exit_code == 0, table.output
    assert "yaml_report/write" in table.output and "prerequisites" in table.output
    default = runner.invoke(main, ["plan", str(source)])
    explicit = runner.invoke(main, ["plan", str(source), "--format", "json"])
    assert default.exit_code == explicit.exit_code == 0
    assert json.loads(default.output) == json.loads(explicit.output)
    assert list(tmp_path.iterdir()) == []


def test_slurm_resources_and_markup_are_literal(tmp_path):
    source = tmp_path / "workflow.yaml"
    source.write_text("""version: 1
name: cluster
tasks:
  job:
    script:
      body: "true"
      backend: slurm
      slurm: {partition: "[research]", cpus-per-task: "8"}
    resources: {cpus: 2, timeout_seconds: 60}
nodes:
  work: {task: job}
""")
    output = render(load(str(source), "{}"))
    assert "bash / slurm" in output
    assert "[research]" in output
    assert '"cpus-per-task": "8"' in output
    assert "CPUs: 2" in output and "Runner timeout: 60s" in output


def tree_output(spec):
    output = io.StringIO()
    Console(file=output, width=240, color_system=None).print(plan_tree(spec))
    return output.getvalue()


def test_tree_diamond_shared_dependencies_and_transitive_reduction():
    spec = load("examples/diamond.py", "{}")
    tree = plan_tree(spec)
    join = tree.children[0]
    assert join.label.plain == "diamond/join (task)"
    assert [child.label.plain for child in join.children] == ["diamond/left (task)", "diamond/right (task)"]
    assert join.children[0].children[0].label.plain == "diamond/seed (task)"
    assert join.children[1].children[0].label.plain == "diamond/seed (task) [shared; see above]"
    assert "@enter" not in tree_output(spec) and "@exit" not in tree_output(spec)
    assert tree_output(spec) == tree_output(spec)


def test_tree_preserves_dependencies_through_nested_barriers():
    spec = load("examples/nested.py", '{"start": 3}')
    join = plan_tree(spec).children[0]
    assert join.label.plain == "nested/join (task)"
    left, right = join.children
    assert left.label.plain == "nested/left/second (task)"
    assert left.children[0].label.plain == "nested/left/first (task)"
    assert left.children[0].children[0].label.plain == "nested/seed (task)"
    assert right.children[0].children[0].label.plain == "nested/seed (task) [shared; see above]"


def test_tree_cli_yaml_no_submission_and_format_conflict(tmp_path, monkeypatch):
    from pathlib import Path

    source = Path("examples/script.yaml").resolve()
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    shorthand = runner.invoke(main, ["plan", str(source), "--tree"])
    explicit = runner.invoke(main, ["plan", str(source), "--format", "tree"])
    assert shorthand.exit_code == explicit.exit_code == 0
    assert shorthand.output == explicit.output
    assert "yaml_report/write (task)" in shorthand.output
    conflict = runner.invoke(main, ["plan", str(source), "--tree", "--format", "json"])
    assert conflict.exit_code == 2 and "Use either" in conflict.output
    assert list(tmp_path.iterdir()) == []


def test_tree_controls_and_empty_workflow():
    from orchestrator import Workflow, compile_workflow

    output = tree_output(load("examples/branches.py", "{}"))
    assert "when branches/choose = large" in output
    assert "trigger: selected_success" in output
    output = tree_output(load("examples/convergence.py", "{}"))
    assert "required finalizer" in output and "(decision)" in output
    assert "no task or decision nodes" in tree_output(compile_workflow(Workflow("empty")))
