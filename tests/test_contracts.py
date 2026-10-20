import json
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from orchestrator import (
    Coordinator,
    Select,
    Task,
    TaskInput,
    TaskOutput,
    Workflow,
    compile_workflow,
    create_run,
)
from orchestrator.artifacts import collect_outputs, stage_inputs, validate_outputs


def run_task(tmp_path, task, inputs=None):
    workflow = Workflow("contract")
    node = workflow.add("task", task, inputs=inputs)
    workflow.expose(**{port.name: Select((node.out(port.name),)) for port in task.outputs})
    path = create_run(compile_workflow(workflow), tmp_path / "runs", allow_local=True)
    return Coordinator(path).run(), path


def writer(outputs, script="from pathlib import Path; Path('out.txt').write_text('payload')", **kwargs):
    return Task(command=(sys.executable, "-c", script), outputs=outputs, **kwargs)


@pytest.mark.parametrize("mode", ["copy", "link"])
@pytest.mark.parametrize("kind", ["file", "directory"])
def test_input_staging(tmp_path, mode, kind):
    source = tmp_path / "source"
    if kind == "file":
        source.write_text("input")
    else:
        source.mkdir()
        (source / "data").write_text("input")
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    port = TaskInput("data", kind=kind, destination="work/data", mode=mode)
    result = stage_inputs({"data": str(source)}, [asdict(port)], attempt)
    staged = Path(result["data"]["path"])
    assert staged.is_symlink() == (mode == "link")
    leaf = staged if kind == "file" else staged / "data"
    leaf.write_text("changed")
    original = source if kind == "file" else source / "data"
    assert original.read_text() == ("input" if mode == "copy" else "changed")


def test_optional_missing_inputs(tmp_path):
    port = asdict(TaskInput("data", kind="file", essential=False))
    assert stage_inputs({"data": str(tmp_path / "missing")}, [port], tmp_path) == {"data": None}
    assert not (tmp_path / "inputs").exists()


def test_missing_essential_input_stops_command(tmp_path):
    task = writer(
        [],
        script="from pathlib import Path; Path('should-not-run').touch()",
        inputs=[TaskInput("data", kind="file", source=str(tmp_path / "missing"))],
    )
    state, path = run_task(tmp_path, task)
    assert state["status"] == "failed"
    assert not list(path.glob("attempts/**/should-not-run"))
    assert "Missing essential input" in state["nodes"]["contract/task"]["reason"]


def test_wrong_optional_input_type_still_fails(tmp_path):
    port = asdict(TaskInput("data", kind="file", essential=False))
    with pytest.raises(ValueError, match="not a file"):
        stage_inputs({"data": str(tmp_path)}, [port], tmp_path)


@pytest.mark.parametrize("mode", ["copy", "link"])
def test_declared_output_collection_and_resume(tmp_path, mode):
    task = writer(
        [TaskOutput("data", kind="file", source="out.txt", destination="published/data.txt", mode=mode)]
    )
    state, path = run_task(tmp_path, task)
    assert state["status"] == "succeeded"
    output = Path(state["outputs"]["data"]["path"])
    assert output.read_text() == "payload"
    assert output.is_symlink() == (mode == "link")
    assert Coordinator(path).run()["revision"] == state["revision"]


def test_optional_missing_output_is_omitted(tmp_path):
    task = writer([TaskOutput("maybe", kind="file", source="missing", essential=False)])
    state, _ = run_task(tmp_path, task)
    assert state["status"] == "succeeded"
    assert state["nodes"]["contract/task"]["outputs"] == {}
    assert state["outputs"] == {"maybe": None}


def test_optional_value_and_input_default(tmp_path):
    task = Task(
        python="examples.tasks:number",
        inputs=[TaskInput("value", essential=False, default=12)],
        outputs=[TaskOutput("value"), TaskOutput("optional", essential=False)],
    )
    state, _ = run_task(tmp_path, task)
    assert state["status"] == "succeeded"
    assert state["outputs"] == {"value": 12, "optional": None}


def test_output_validator_failure_prevents_collection(tmp_path):
    destination = tmp_path / "export.txt"
    task = writer(
        [
            TaskOutput(
                "data",
                kind="file",
                source="out.txt",
                destination=str(destination),
                validator="examples.tasks:nonempty",
            )
        ],
        script="from pathlib import Path; Path('out.txt').touch()",
    )
    state, _ = run_task(tmp_path, task)
    assert state["status"] == "failed"
    assert "validator rejected" in state["nodes"]["contract/task"]["reason"]
    assert not destination.exists()


def test_external_output_never_overwrites(tmp_path):
    destination = tmp_path / "export.txt"
    destination.write_text("original")
    task = writer([TaskOutput("data", kind="file", source="out.txt", destination=str(destination))])
    state, _ = run_task(tmp_path, task)
    assert state["status"] == "failed"
    assert destination.read_text() == "original"


def test_readonly_reconciliation_does_not_recollect_or_run_validator(tmp_path):
    published = tmp_path / "published"
    published.write_text("result")
    contract = [
        asdict(
            TaskOutput(
                "data",
                kind="file",
                source="gone",
                destination="published",
                validator="nonexistent.module:validator",
            )
        )
    ]
    result = validate_outputs({"data": {"path": str(published)}}, contract, tmp_path)
    assert result["data"]["path"] == str(published)


def test_input_source_reference_adds_dependency(tmp_path):
    workflow = Workflow("source")
    first = workflow.add("first", writer([TaskOutput("data", kind="file", source="out.txt")]))
    second = workflow.add(
        "second",
        Task(
            python="examples.tasks:summarize_file",
            inputs=[TaskInput("source", kind="file", source=first.out("data"))],
            outputs=[TaskOutput("lines")],
        ),
    )
    workflow.expose(lines=second.out("lines"))
    spec = compile_workflow(workflow)
    assert "source/first" in spec["nodes"]["source/second"]["deps"]
    assert spec == json.loads(json.dumps(spec))
    path = create_run(spec, tmp_path / "runs", allow_local=True)
    assert Coordinator(path).run()["outputs"] == {"lines": 1}


def test_invocation_overrides_input_source():
    task = Task(
        python="examples.tasks:number", inputs=[TaskInput("value", source=3)], outputs=[TaskOutput("value")]
    )
    workflow = Workflow("override")
    workflow.add("task", task, inputs={"value": 7})
    spec = compile_workflow(workflow)
    assert spec["nodes"]["override/task"]["inputs"] == {"value": 7}


@pytest.mark.parametrize(
    "ports",
    [
        {"inputs": [TaskInput("data"), TaskInput("data")]},
        {"outputs": [TaskOutput("data"), TaskOutput("data")]},
        {"inputs": [TaskInput("data", kind="file", source="x", mode="move")]},
        {"inputs": [TaskInput("data", kind="file", source="x", destination="../escape")]},
        {"inputs": [TaskInput("data", kind="file", source="x", destination="input.json")]},
        {"outputs": [TaskOutput("data", kind="file", source="../escape")]},
        {"outputs": [TaskOutput("data", destination="unused")]},
        {"outputs": [TaskOutput("data", validator="not-importable")]},
        {
            "outputs": [
                TaskOutput("a", kind="file", destination="same"),
                TaskOutput("b", kind="file", destination="same"),
            ]
        },
        {
            "inputs": [TaskInput("data", kind="file", source="x", destination="overlap")],
            "outputs": [TaskOutput("data", kind="file", destination="overlap/output")],
        },
    ],
)
def test_invalid_port_contracts_rejected(ports):
    flow = Workflow("invalid")
    flow.add("task", Task(command=("true",), **ports))
    with pytest.raises(ValueError):
        compile_workflow(flow)


def test_symlink_destination_escape_rejected(tmp_path):
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (attempt / "escape").symlink_to(outside, target_is_directory=True)
    source = tmp_path / "source"
    source.write_text("input")
    port = asdict(TaskInput("data", kind="file", destination="escape/input"))
    with pytest.raises(ValueError, match="escapes"):
        stage_inputs({"data": str(source)}, [port], attempt)


def test_directory_collection(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "data").write_text("contents")
    port = asdict(TaskOutput("data", kind="directory", source="source", destination="collected"))
    result = collect_outputs({}, [port], tmp_path)
    assert (Path(result["data"]["path"]) / "data").read_text() == "contents"


def test_unknown_spec_version_rejected(tmp_path):
    spec = compile_workflow(Workflow("empty"))
    spec["schema_version"] = 1
    with pytest.raises(ValueError, match="Unsupported workflow schema"):
        create_run(spec, tmp_path, allow_local=True)


def test_installed_cli_staging_example(tmp_path):
    import os
    import subprocess

    # Use the console script, without pytest's sys.path/PYTHONPATH conveniences.
    executable = Path(sys.executable).parent / "orchestrator"
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [
            str(executable),
            "--root",
            str(tmp_path / "runs"),
            "run",
            "examples/staging.py",
            "--allow-local",
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    state = json.loads(completed.stdout)
    assert state["outputs"]["lines"] == 2
    assert Path(state["outputs"]["report"]["path"]).is_file()


def test_cli_preserves_uncertain_exit_code():
    import click

    from orchestrator.cli import errors

    @errors
    def unresolved():
        raise click.exceptions.Exit(2)

    with pytest.raises(click.exceptions.Exit) as exc:
        unresolved()
    assert exc.value.exit_code == 2
