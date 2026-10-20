import json
import runpy
import shlex
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from orchestrator import (
    Coordinator,
    Directory,
    Retry,
    Script,
    Task,
    TaskOutput,
    Workflow,
    compile_workflow,
    create_run,
)
from orchestrator.cli import main
from orchestrator.storage import Store


def cwd_task(**kwargs):
    return Task(
        script=Script(body="pwd > cwd.txt"),
        outputs=[TaskOutput("cwd", kind="file", source="cwd.txt")],
        **kwargs,
    )


def state_at(path):
    store = Store(path / "state.sqlite")
    try:
        return store.load()
    finally:
        store.close()


def test_root_parent_absolute_and_override_bases(tmp_path):
    scratch = tmp_path / "scratch"
    child = Workflow("child", directory="child")
    child.add("inherited", cwd_task())
    child.add("relative", cwd_task(directory="work"))
    child.add("rooted", cwd_task(directory=Directory("fast", relative_to="root")))
    child.add("overridden", cwd_task(directory="unused"), directory=str(scratch / "override"))
    flow = Workflow("dirs", directory=str(scratch))
    flow.add("nested", child)
    flow.add("root_default", cwd_task())
    path = create_run(compile_workflow(flow), tmp_path / "runs", allow_local=True)
    assert not scratch.exists()  # Resolving bases does not create task directories.
    bases = state_at(path)["directory_bases"]
    assert bases == {
        "dirs/nested/inherited": str(scratch / "child"),
        "dirs/nested/relative": str(scratch / "child/work"),
        "dirs/nested/rooted": str(path / "fast"),
        "dirs/nested/overridden": str(scratch / "override"),
        "dirs/root_default": str(scratch),
    }
    flow.directory = str(tmp_path / "changed_after_creation")
    state = Coordinator(path).run()
    assert state["status"] == "succeeded"
    for name, node in state["nodes"].items():
        if not node["attempts"]:
            continue
        attempt = node["attempts"][0]
        actual = Path(attempt["working_directory"])
        assert Path(node["outputs"]["cwd"]["path"]).read_text().strip() == str(actual)
        assert (
            actual
            == Path(bases[name])
            / ("" if actual.is_relative_to(path) else path.name)
            / "attempts"
            / name
            / attempt["id"]
        )
        assert (path / attempt["path"]) == actual
    assert (path / "state.sqlite").exists()
    assert not list(scratch.rglob("state.sqlite"))


def test_nested_invocation_directory_override(tmp_path):
    child = Workflow("child", directory="unused")
    child.add("work", cwd_task(directory="inside"))
    root = Workflow("root", directory=str(tmp_path / "external"))
    root.add("child", child, directory=Directory("replacement", "root"))
    path = create_run(compile_workflow(root), tmp_path / "runs", allow_local=True)
    assert state_at(path)["directory_bases"]["root/child/work"] == str(path / "replacement/inside")


def test_yaml_directory_forms_and_external_cli_inspection(tmp_path):
    source = tmp_path / "workflow.yaml"
    scratch = tmp_path / "scratch"
    source.write_text(f"""version: 1
name: yaml_dirs
directory: {{path: workspace, relative_to: root}}
tasks:
  work:
    directory: child
    script: {{body: "printf hello > result.txt"}}
    outputs: [{{name: result, kind: file, source: result.txt}}]
nodes:
  inherited: {{task: work}}
  rooted: {{task: work, directory: {{path: other, relative_to: root}}}}
  external: {{task: work, directory: {json.dumps(str(scratch))}}}
outputs:
  result: {{$ref: external.result}}
""")
    runner = CliRunner()
    root = tmp_path / "runs"
    result = runner.invoke(main, ["--root", str(root), "run", str(source), "--allow-local", "--json"])
    assert result.exit_code == 0, result.output
    state = json.loads(result.stdout)
    path = root / state["run_id"]
    assert state["directory_bases"]["yaml_dirs/inherited"] == str(path / "workspace/child")
    assert state["directory_bases"]["yaml_dirs/rooted"] == str(path / "other")
    assert Path(state["outputs"]["result"]["path"]).read_text() == "hello"
    inspected = runner.invoke(main, ["--root", str(root), "inspect", path.name, "yaml_dirs/external"])
    assert inspected.exit_code == 0 and str(scratch) in inspected.stdout
    script = runner.invoke(main, ["--root", str(root), "script", path.name, "yaml_dirs/external"])
    assert script.exit_code == 0 and str(scratch) in script.stdout


def test_retries_use_frozen_base_and_separate_scripts(tmp_path):
    scratch = tmp_path / "scratch"
    marker = shlex.quote(str(tmp_path / "first_attempt"))
    source = tmp_path / "job.sh"
    source.write_text(f"if mkdir {marker}; then exit 1; fi\npwd > cwd.txt")
    task = Task(
        script=Script(path=str(source)),
        directory=str(scratch),
        retry=Retry(max_attempts=2),
        outputs=[TaskOutput("cwd", kind="file", source="cwd.txt")],
    )
    flow = Workflow("retry")
    flow.add("work", task)
    path = create_run(compile_workflow(flow), tmp_path / "runs", allow_local=True)
    source.write_text("exit 99")
    state = Coordinator(path).run()
    assert state["status"] == "succeeded"
    attempts = state["nodes"]["retry/work"]["attempts"]
    assert len(attempts) == 2
    assert attempts[0]["status"] == "failed" and attempts[1]["status"] == "succeeded"
    assert attempts[0]["path"] != attempts[1]["path"]
    assert attempts[0]["script"]["sha256"] != attempts[1]["script"]["sha256"]
    assert all(Path(a["path"]).is_relative_to(scratch / path.name) for a in attempts)
    assert Coordinator(path).run()["revision"] == state["revision"]


class LostAcknowledgement:
    def __init__(self, publish):
        self.publish = publish
        self.calls = 0

    def submit(self, directory):
        self.calls += 1
        if self.publish:
            manifest = json.loads((directory / "input.json").read_text())
            (directory / "result.json").write_text(
                json.dumps(
                    {
                        **{name: manifest[name] for name in ("run_id", "node_id", "attempt_id")},
                        "status": "succeeded",
                        "outputs": {},
                    }
                )
            )
        raise KeyboardInterrupt()

    def observe(self, directory, job):
        return "result" if (directory / "result.json").exists() else "uncertain"

    def cancel(self, directory, job):
        return False


@pytest.mark.parametrize("publish", [True, False])
def test_external_submission_uncertainty_never_resubmits(tmp_path, publish):
    flow = Workflow("lost", directory=str(tmp_path / "scratch"))
    flow.add("work", Task(script=Script(body="true")))
    path = create_run(compile_workflow(flow), tmp_path / "runs", allow_local=True)
    backend = LostAcknowledgement(publish)
    with pytest.raises(KeyboardInterrupt):
        Coordinator(path, backend).run()
    state = Coordinator(path, backend).run()
    assert state["status"] == ("succeeded" if publish else "uncertain")
    assert backend.calls == 1 and len(state["nodes"]["lost/work"]["attempts"]) == 1


def test_vanished_scratch_stays_uncertain_without_recreation(tmp_path):
    flow = Workflow("vanished", directory=str(tmp_path / "scratch"))
    flow.add("work", Task(script=Script(body="true")))
    path = create_run(compile_workflow(flow), tmp_path / "runs", allow_local=True)
    backend = LostAcknowledgement(False)
    with pytest.raises(KeyboardInterrupt):
        Coordinator(path, backend).run()
    store = Store(path / "state.sqlite")
    state = store.load()
    attempt = state["nodes"]["vanished/work"]["attempts"][0]
    attempt["created_at"] -= 2
    directory = Path(attempt["path"])
    store.save(state, "test_elapsed_grace")
    store.close()
    shutil.rmtree(directory)
    state = Coordinator(path).run()
    assert state["status"] == "uncertain"
    assert not directory.exists() and backend.calls == 1
    assert state["nodes"]["vanished/work"]["attempts"][0]["observation_error"]


@pytest.mark.parametrize("example", ["branches", "convergence"])
def test_conditional_work_and_finalizers_keep_directory_semantics(tmp_path, example):
    flow = runpy.run_path(f"examples/{example}.py")["workflow"]
    flow.directory = str(tmp_path / "scratch")
    path = create_run(compile_workflow(flow), tmp_path / "runs", allow_local=True)
    state = Coordinator(path).run()
    assert state["status"] == "succeeded"
    if example == "branches":
        assert not state["nodes"]["branches/small"]["attempts"]
        assert not (tmp_path / "scratch" / path.name / "attempts/branches/small").exists()
    else:
        assert state["outputs"] == {"value": 8}
        assert not state["nodes"]["experiment/optimize/step_5"]["attempts"]
        assert not state["nodes"]["experiment/optimize/step_6"]["attempts"]
        assert state["nodes"]["experiment/optimize/cleanup"]["status"] == "succeeded"
    assert all(
        Path(attempt["path"]).is_relative_to(tmp_path / "scratch" / path.name)
        for node in state["nodes"].values()
        for attempt in node["attempts"]
    )


def test_schema_v3_resume_keeps_default_layout(tmp_path):
    flow = Workflow("legacy")
    flow.add("work", Task(command=("true",)))
    spec = compile_workflow(flow)
    spec["schema_version"] = 3
    for scope in spec["scopes"].values():
        scope.pop("directory")
    spec["nodes"]["legacy/work"]["task"].pop("directory")
    path = create_run(spec, tmp_path, allow_local=True)
    store = Store(path / "state.sqlite")
    state = store.load()
    state.pop("directory_bases")
    store.save(state, "test_old_snapshot")
    store.close()
    state = Coordinator(path).run()
    assert state["status"] == "succeeded"
    assert state["nodes"]["legacy/work"]["attempts"][0]["path"].startswith("attempts/legacy/work/")


def test_new_schema_rejects_missing_frozen_bases(tmp_path):
    flow = Workflow("pinned")
    flow.add("work", cwd_task())
    path = create_run(compile_workflow(flow), tmp_path, allow_local=True)
    store = Store(path / "state.sqlite")
    state = store.load()
    state.pop("directory_bases")
    store.save(state, "test_missing_bases")
    store.close()
    with pytest.raises(ValueError, match="directory bases"):
        Coordinator(path).run()
    assert not (path / "attempts").exists()


def test_external_slurm_script_and_lost_ack_keep_exact_paths(tmp_path):
    flow = Workflow("cluster", directory=str(tmp_path / "shared"))
    flow.add("work", Task(script=Script(body="true", backend="slurm")))
    path = create_run(compile_workflow(flow), tmp_path / "runs")
    backend = LostAcknowledgement(True)
    with pytest.raises(KeyboardInterrupt):
        Coordinator(path, backend).run()
    attempt = state_at(path)["nodes"]["cluster/work"]["attempts"][0]
    directory = Path(attempt["path"])
    script = (directory / "job.sh").read_bytes()
    assert f"#SBATCH --chdir={directory}".encode() in script
    assert f"#SBATCH --output={directory}/stdout.log".encode() in script
    assert Coordinator(path, backend).run()["status"] == "succeeded"
    assert backend.calls == 1 and (directory / "job.sh").read_bytes() == script


def test_directory_creation_failure_does_not_block_independent_work(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    flow = Workflow("failure")
    flow.add("blocked", cwd_task(directory=str(blocked)))
    flow.add("independent", cwd_task())
    path = create_run(compile_workflow(flow), tmp_path / "runs", allow_local=True)
    state = Coordinator(path).run()
    assert state["status"] == "failed"
    assert state["nodes"]["failure/blocked"]["status"] == "failed"
    assert not state["nodes"]["failure/blocked"]["attempts"]
    assert state["nodes"]["failure/independent"]["status"] == "succeeded"


def test_cancellation_keeps_external_finalizer_eligible(tmp_path):
    flow = Workflow("cancel", directory=str(tmp_path / "scratch"))
    work = flow.add("work", Task(command=("true",)))
    flow.add("cleanup", Task(command=("true",)), after=(work,), trigger="all_terminal", finalizer=True)
    path = create_run(compile_workflow(flow), tmp_path / "runs", allow_local=True)

    class Backend:
        def publish(self, directory, status):
            manifest = json.loads((directory / "input.json").read_text())
            (directory / "result.json").write_text(
                json.dumps(
                    {
                        **{key: manifest[key] for key in ("run_id", "node_id", "attempt_id")},
                        "status": status,
                        "outputs": {},
                    }
                )
            )

        def submit(self, directory):
            manifest = json.loads((directory / "input.json").read_text())
            if manifest["node_id"].endswith("/cleanup"):
                self.publish(directory, "succeeded")
            else:
                (path / "cancel.request").touch()
            return {"id": "fake"}

        def observe(self, directory, job):
            return "result" if (directory / "result.json").exists() else "running"

        def cancel(self, directory, job):
            self.publish(directory, "cancelled")
            return True

    state = Coordinator(path, Backend()).run()
    assert state["status"] == "cancelled"
    assert state["nodes"]["cancel/work"]["status"] == "cancelled"
    assert state["nodes"]["cancel/cleanup"]["status"] == "succeeded"
    assert all(
        Path(attempt["path"]).is_relative_to(tmp_path / "scratch" / path.name)
        for node in state["nodes"].values()
        for attempt in node["attempts"]
    )


@pytest.mark.parametrize(
    "directory",
    [
        "",
        Directory("relative", "absolute"),
        Directory("/absolute", "root"),
        Directory("work", "invalid"),
        Directory("work", False),
    ],
)
def test_invalid_directory_settings(directory):
    flow = Workflow("invalid", directory=directory)
    flow.add("work", Task(command=("true",)))
    with pytest.raises(ValueError, match="[Dd]irectory|[Aa]bsolute"):
        compile_workflow(flow)
