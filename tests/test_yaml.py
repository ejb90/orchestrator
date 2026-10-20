import copy
import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from orchestrator import (
    Coordinator,
    Input,
    Task,
    TaskInput,
    TaskOutput,
    Workflow,
    compile_workflow,
    create_run,
    load_yaml,
)
from orchestrator.cli import main
from orchestrator.spec import VERSION


def document():
    return {
        "version": 1,
        "name": "static",
        "inputs": ["start"],
        "tasks": {
            "double": {
                "python": "examples.tasks:double",
                "inputs": [{"name": "value"}],
                "outputs": [{"name": "value"}],
            }
        },
        "nodes": {
            "first": {"task": "double", "inputs": {"value": {"$input": "start"}}},
            "second": {"task": "double", "inputs": {"value": {"$ref": "first.value"}}},
            "parallel": {"task": "double", "inputs": {"value": 5}},
        },
        "outputs": {"value": {"$ref": "second.value"}},
    }


def write_yaml(tmp_path, data):
    source = tmp_path / "workflow.yaml"
    source.write_text(yaml.safe_dump(data, sort_keys=False))
    return source


def test_same_spec_as_python_and_real_execution(tmp_path):
    task = Task(python="examples.tasks:double", inputs=[TaskInput("value")], outputs=[TaskOutput("value")])
    flow = Workflow("static", inputs=("start",))
    first = flow.add("first", task, inputs={"value": Input("start")})
    second = flow.add("second", task, inputs={"value": first.out("value")})
    flow.add("parallel", task, inputs={"value": 5})
    flow.expose(value=second.out("value"))
    spec = load_yaml(write_yaml(tmp_path, document()), {"start": 3})
    assert spec == compile_workflow(flow, {"start": 3})
    assert spec == load_yaml(tmp_path / "workflow.yaml", {"start": 3})
    state = Coordinator(create_run(spec, tmp_path / "runs", allow_local=True)).run()
    assert state["status"] == "succeeded"
    assert state["outputs"] == {"value": 12}


def test_cli_script_validate_plan_render_run_resume(tmp_path):
    runner = CliRunner()
    source = str(Path("examples/script.yaml").resolve())
    assert runner.invoke(main, ["validate", source]).exit_code == 0
    plan = runner.invoke(main, ["plan", source])
    assert plan.exit_code == 0, plan.output
    assert json.loads(plan.output)["schema_version"] == VERSION
    preview = tmp_path / "preview"
    result = runner.invoke(main, ["render", source, "--output", str(preview)])
    assert result.exit_code == 0, result.output
    assert "printf" in next(preview.rglob("job.sh")).read_text()
    assert not list(tmp_path.rglob("state.sqlite"))
    root = tmp_path / "runs"
    result = runner.invoke(main, ["--root", str(root), "run", source, "--allow-local", "--json"])
    assert result.exit_code == 0, result.output
    run_id = next(root.iterdir()).name
    # Resume uses the persisted spec, without reloading the YAML authoring file.
    result = runner.invoke(main, ["--root", str(root), "resume", run_id, "--json"])
    assert result.exit_code == 0, result.output
    state = json.loads(result.output)
    assert Path(state["outputs"]["report"]["path"]).read_text() == "Hello from a YAML workflow\n"
    assert len(state["nodes"]["yaml_report/write"]["attempts"]) == 1


def test_cli_yml_and_inputs(tmp_path):
    source = write_yaml(tmp_path, document()).rename(tmp_path / "workflow.yml")
    result = CliRunner().invoke(main, ["plan", str(source), "--inputs", '{"start": 7}'])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["nodes"]["static/@enter"]["inputs"] == {"start": 7}
    result = CliRunner().invoke(main, ["plan", str(source), "--inputs", "[]"])
    assert result.exit_code != 0 and "JSON object" in result.output


@pytest.mark.parametrize("location", ["default", "invocation"])
def test_script_and_artifact_paths_relative_to_yaml(tmp_path, monkeypatch, location):
    definitions = tmp_path / "definitions"
    definitions.mkdir()
    (definitions / "job.sh").write_text("cat inputs/data > report.txt")
    (definitions / "seed.txt").write_text("original")
    port = {"name": "data", "kind": "file"}
    node = {"task": "copy"}
    if location == "default":
        port["source"] = "seed.txt"
    else:
        node["inputs"] = {"data": "seed.txt"}
    source = write_yaml(
        definitions,
        {
            "version": 1,
            "name": "paths",
            "tasks": {
                "copy": {
                    "script": {"path": "job.sh"},
                    "inputs": [port],
                    "outputs": [{"name": "report", "kind": "file", "source": "report.txt"}],
                }
            },
            "nodes": {"copy": node},
            "outputs": {"report": {"$ref": "copy.report"}},
        },
    )
    monkeypatch.chdir(tmp_path)
    spec = load_yaml(source)
    (definitions / "job.sh").write_text("exit 99")
    state = Coordinator(create_run(spec, tmp_path / "runs", allow_local=True)).run()
    assert state["status"] == "succeeded"
    assert Path(state["outputs"]["report"]["path"]).read_text() == "original"


@pytest.mark.parametrize(
    "text,match",
    [
        ("", "mapping"),
        ("name: test\nname: other", "Duplicate YAML key"),
        ("version: 1\nname: [", "workflow.yaml"),
        ("name: &name test\noutputs: *name", "aliases"),
        ("!!python/object/apply:os.system ['touch SHOULD_NOT_RUN']", "constructor"),
        ("version: 1\nname: test\nnodes: {}\noutputs: {date: 2026-10-20}", "JSON-compatible"),
        ("version: 1\nname: test\nnodes: {}\noutputs: {x: .nan}", "JSON-compatible"),
        ("version: 1\nname: test\nnodes: {}\noutputs: {on: yes}", "string"),
    ],
)
def test_invalid_yaml(tmp_path, monkeypatch, text, match):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "workflow.yaml"
    source.write_text(text)
    with pytest.raises(ValueError, match=match):
        load_yaml(source)
    assert not (tmp_path / "SHOULD_NOT_RUN").exists()


@pytest.mark.parametrize(
    "change,match",
    [
        (lambda d: d.update(version=2), "version"),
        (lambda d: d.update(version=True), "version"),
        (lambda d: d.update(typo=1), "unknown fields"),
        (lambda d: d.update(outputs={"$ref": "first.value"}), "binding names"),
        (lambda d: d["nodes"]["first"].update(inputs={"$input": "start"}), "binding names"),
        (lambda d: d["tasks"]["double"].update(retrys={}), "unknown fields"),
        (lambda d: d["tasks"]["double"].update(env={"COUNT": 2}), "invalid type"),
        (lambda d: d["tasks"]["double"].update(resources={"cpus": True}), "invalid type"),
        (lambda d: d["tasks"]["double"].update(inputs=["value"]), "mapping"),
        (lambda d: d["tasks"]["double"].update(command=["true"]), "exactly one"),
        (lambda d: d["nodes"]["first"].update(task="missing"), "unknown task"),
        (lambda d: d["nodes"]["first"].update(after="second"), "list"),
        (lambda d: d["nodes"]["first"].update(finalizer="false"), "Boolean"),
        (lambda d: d["nodes"]["first"].update(after=["missing"]), "unknown dependency"),
        (lambda d: d["nodes"]["first"].update(after=["second"]), "cycle"),
        (lambda d: d["nodes"]["second"]["inputs"].update(value={"$ref": "first.missing"}), "reference"),
        (lambda d: d["nodes"]["second"]["inputs"].update(value={"$ref": "first"}), "node.output"),
        (
            lambda d: d["nodes"]["second"]["inputs"].update(value={"$input": "missing"}),
            "Unknown workflow input",
        ),
    ],
)
def test_invalid_definitions_use_clear_errors(tmp_path, change, match):
    data = copy.deepcopy(document())
    change(data)
    source = write_yaml(tmp_path, data)
    with pytest.raises(ValueError, match=match):
        load_yaml(source, {"start": 3})
    result = CliRunner().invoke(main, ["validate", str(source), "--inputs", '{"start": 3}'])
    assert result.exit_code == 1 and "Error:" in result.output


def test_command_resources_retry_and_finalizer(tmp_path):
    source = write_yaml(
        tmp_path,
        {
            "version": 1,
            "name": "cleanup",
            "resources": {"cpus": 2},
            "max_active": 1,
            "tasks": {
                "fail": {"command": ["false"], "retry": {"max_attempts": 2, "categories": ["application"]}},
                "finish": {"command": ["touch", "cleaned"]},
            },
            "nodes": {
                "work": {"task": "fail", "resources": {"cpus": 3}},
                "cleanup": {
                    "task": "finish",
                    "after": ["work"],
                    "trigger": "all_terminal",
                    "finalizer": True,
                },
            },
        },
    )
    spec = load_yaml(source)
    assert spec["nodes"]["cleanup/work"]["task"]["resources"]["cpus"] == 3
    assert spec["nodes"]["cleanup/cleanup"]["task"]["resources"]["cpus"] == 2
    path = create_run(spec, tmp_path / "runs", allow_local=True)
    state = Coordinator(path).run()
    assert state["status"] == "failed"
    assert len(state["nodes"]["cleanup/work"]["attempts"]) == 2
    assert state["nodes"]["cleanup/cleanup"]["status"] == "succeeded"
    assert len(list(path.rglob("cleaned"))) == 1
