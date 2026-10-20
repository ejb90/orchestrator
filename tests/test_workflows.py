import json
import runpy
from pathlib import Path

import pytest
from click.testing import CliRunner

from orchestrator import (
    Coordinator,
    Input,
    Retry,
    Task,
    TaskInput,
    TaskOutput,
    Workflow,
    compile_workflow,
    create_run,
    iterate,
)
from orchestrator.cli import main
from orchestrator.engine import readiness
from orchestrator.runner import atomic_json
from orchestrator.storage import Store, ownership

NUMBER = Task(python="examples.tasks:number", inputs=[TaskInput("value")], outputs=[TaskOutput("value")])


def execute(flow, tmp_path, inputs=None, **kwargs):
    path = create_run(compile_workflow(flow, inputs), tmp_path, allow_local=True, **kwargs)
    return Coordinator(path).run(), path


@pytest.mark.parametrize(
    "example,inputs,expected",
    [
        ("diamond", {}, 12),
        ("nested", {"start": 3}, 24),
        ("branches", {}, 100),
        ("convergence", {}, 8),
    ],
)
def test_examples(example, inputs, expected, tmp_path):
    flow = runpy.run_path(f"examples/{example}.py")["workflow"]
    state, path = execute(flow, tmp_path, inputs)
    assert state["status"] == "succeeded", state
    assert state["outputs"] == {"value": expected}
    before = state["revision"]
    resumed = Coordinator(path).run()
    assert resumed["revision"] == before
    if example == "convergence":
        for i in (5, 6):
            assert state["nodes"][f"experiment/optimize/step_{i}"]["attempts"] == []
        assert state["nodes"]["experiment/optimize/cleanup"]["status"] == "succeeded"
        assert len(state["decisions"]) == 4


def test_command_artifact(tmp_path):
    flow = runpy.run_path("examples/command.py")["workflow"]
    state, _ = execute(flow, tmp_path)
    assert state["status"] == "succeeded"
    assert Path(state["outputs"]["answer"]["path"]).read_text() == "42"


def test_compile_nested_deterministic():
    flow = runpy.run_path("examples/nested.py")["workflow"]
    a = compile_workflow(flow, {"start": 1})
    b = compile_workflow(flow, {"start": 1})
    assert a == json.loads(json.dumps(b))
    assert "nested/left/@exit" in a["nodes"]["nested/@exit"]["deps"]


@pytest.mark.parametrize("mode", ["duplicate", "cycle", "reference", "output", "input", "resource"])
def test_validation(mode):
    from orchestrator import Ref, Resources

    flow = Workflow("bad")
    a = flow.add("a", NUMBER, inputs={"value": 1})
    if mode == "duplicate":
        flow.add("a", NUMBER, inputs={"value": 2})
    elif mode == "cycle":
        b = flow.add("b", NUMBER, inputs={"value": a.out("value")})
        flow.serial(b, a)
    elif mode == "reference":
        flow.add("b", NUMBER, inputs={"value": Ref("unknown", "value")})
    elif mode == "output":
        flow.expose(value=a.out("missing"))
    elif mode == "input":
        flow.add("b", NUMBER, inputs={"value": Input("unknown")})
    else:
        flow.add("b", NUMBER, inputs={"value": 1}, resources=Resources(cpus=0))
    with pytest.raises(ValueError):
        compile_workflow(flow)


def test_failure_retries_and_finalizer(tmp_path):
    flow = Workflow("failure")
    failure = flow.add("fail", Task(python="examples.tasks:fail", retry=Retry(max_attempts=2)))
    flow.add("blocked", NUMBER, inputs={"value": 1}, after=(failure,))
    flow.add(
        "cleanup",
        Task(python="examples.tasks:cleanup", outputs=[TaskOutput("report", kind="file")]),
        after=(failure,),
        trigger="all_terminal",
        finalizer=True,
    )
    state, _ = execute(flow, tmp_path)
    assert state["status"] == "failed"
    assert len(state["nodes"]["failure/fail"]["attempts"]) == 2
    assert state["nodes"]["failure/blocked"]["status"] == "skipped"
    assert state["nodes"]["failure/cleanup"]["status"] == "succeeded"
    paths = [a["path"] for a in state["nodes"]["failure/fail"]["attempts"]]
    assert len(set(paths)) == 2


def test_required_finalizer_failure_overrides_convergence(tmp_path):
    step = Task(python="examples.tasks:increment", inputs=[TaskInput("value")], outputs=[TaskOutput("value")])
    flow = iterate("loop", step, initial=3, max_iterations=6, policy="examples.tasks:converge")
    from orchestrator import Handle

    flow.add(
        "cleanup",
        Task(python="examples.tasks:fail"),
        after=tuple(Handle(n.id) for n in flow.nodes),
        trigger="all_terminal",
        finalizer=True,
    )
    state, _ = execute(flow, tmp_path)
    assert state["status"] == "failed"
    assert state["nodes"]["loop/step_2"]["attempts"] == []


def test_local_opt_in(tmp_path):
    with pytest.raises(ValueError, match="explicit"):
        create_run(compile_workflow(Workflow("empty")), tmp_path)


def test_single_owner(tmp_path):
    with ownership(tmp_path), pytest.raises(RuntimeError, match="Another coordinator"), ownership(tmp_path):
        pass


class CrashAfterAcceptance:
    """Persisted intent, side effect succeeded, acknowledgement lost."""

    def __init__(self, publish=True):
        self.calls = 0
        self.publish = publish

    def submit(self, directory):
        self.calls += 1
        manifest = json.loads((directory / "input.json").read_text())
        if self.publish:
            atomic_json(
                directory / "result.json",
                {
                    **{k: manifest[k] for k in ("run_id", "node_id", "attempt_id")},
                    "status": "succeeded",
                    "outputs": {"value": 7},
                },
            )
        raise KeyboardInterrupt()

    def observe(self, directory, job):
        return "result" if (directory / "result.json").exists() else "uncertain"

    def cancel(self, directory, job):
        return False


@pytest.mark.parametrize("publish", [True, False])
def test_recover_lost_acknowledgement(tmp_path, publish):
    flow = Workflow("recovery")
    a = flow.add("a", NUMBER, inputs={"value": 7})
    flow.expose(value=a.out("value"))
    path = create_run(compile_workflow(flow), tmp_path, allow_local=True)
    backend = CrashAfterAcceptance(publish)
    with pytest.raises(KeyboardInterrupt):
        Coordinator(path, backend).run()
    state = Coordinator(path, backend).run()
    assert state["status"] == ("succeeded" if publish else "uncertain")
    assert backend.calls == 1
    assert len(state["nodes"]["recovery/a"]["attempts"]) == 1
    if publish:
        assert state["outputs"] == {"value": 7}


def test_stale_result_not_accepted(tmp_path):
    flow = Workflow("stale")
    flow.add("a", NUMBER, inputs={"value": 7})
    path = create_run(compile_workflow(flow), tmp_path, allow_local=True)
    backend = CrashAfterAcceptance()
    with pytest.raises(KeyboardInterrupt):
        Coordinator(path, backend).run()
    result_path = next(path.glob("attempts/**/result.json"))
    result = json.loads(result_path.read_text())
    result["attempt_id"] = "previous-attempt"
    atomic_json(result_path, result)
    state = Coordinator(path, backend).run()
    assert state["status"] == "uncertain"
    assert backend.calls == 1


def test_events_atomic_and_ordered(tmp_path):
    flow = Workflow("events")
    flow.add("a", NUMBER, inputs={"value": 3})
    state, path = execute(flow, tmp_path)
    store = Store(path / "state.sqlite")
    try:
        events = store.events()
        assert [e["revision"] for e in events] == list(range(1, state["revision"] + 1))
        kinds = [e["kind"] for e in events]
        assert kinds.index("submission_intent") < kinds.index("submission_acknowledged")
    finally:
        store.close()


def test_cli(tmp_path):
    runner = CliRunner()
    assert runner.invoke(main, ["validate", "examples/diamond.py"]).exit_code == 0
    assert runner.invoke(main, ["plan", "examples/branches.py"]).exit_code == 0
    result = runner.invoke(main, ["--root", str(tmp_path), "run", "examples/diamond.py", "--allow-local"])
    assert result.exit_code == 0, result.output
    run_id = next(tmp_path.iterdir()).name
    result = runner.invoke(main, ["--root", str(tmp_path), "status", run_id, "--json"])
    assert json.loads(result.output)["status"] == "succeeded"


def test_selected_join_rule():
    node = {"deps": ["a", "b"], "when": None, "trigger": "selected_success"}
    assert readiness(node, {"a": {"status": "succeeded"}, "b": {"status": "skipped"}}) == "ready"
    assert readiness(node, {"a": {"status": "failed"}, "b": {"status": "skipped"}}) == "skipped"


def test_timeout(tmp_path):
    from orchestrator import Resources

    flow = Workflow("timeout")
    flow.add(
        "slow",
        Task(
            python="examples.tasks:slow",
            inputs=[TaskInput("value"), TaskInput("seconds")],
            outputs=[TaskOutput("value")],
            resources=Resources(timeout_seconds=0.1),
        ),
        inputs={"value": 1, "seconds": 10},
    )
    state, _ = execute(flow, tmp_path)
    assert state["status"] == "failed"
    assert state["nodes"]["timeout/slow"]["attempts"][0]["result"]["category"] == "timeout"


def test_scope_concurrency_and_parallelism(tmp_path):
    slow = Task(
        python="examples.tasks:slow",
        inputs=[TaskInput("value"), TaskInput("seconds")],
        outputs=[TaskOutput("value")],
    )
    child = Workflow("child", max_active=1)
    child.add("a", slow, inputs={"value": 1, "seconds": 0.2})
    child.add("b", slow, inputs={"value": 2, "seconds": 0.2})
    flow = Workflow("limits")
    flow.add("left", child)
    flow.add("right", child)
    state, _ = execute(flow, tmp_path, max_active=2)
    assert state["status"] == "succeeded"
    attempts = {n: s["attempts"][0] for n, s in state["nodes"].items() if s["attempts"]}
    for branch in ("left", "right"):
        a, b = attempts[f"limits/{branch}/a"], attempts[f"limits/{branch}/b"]
        assert a["finished_at"] <= b["created_at"]
    left, right = attempts["limits/left/a"], attempts["limits/right/a"]
    assert max(left["created_at"], right["created_at"]) < min(left["finished_at"], right["finished_at"])


def test_resume_real_running_process(tmp_path):
    from orchestrator.backends import LocalBackend

    class InterruptOnce(LocalBackend):
        def __init__(self):
            super().__init__()
            self.first = True

        def observe(self, directory, job):
            if self.first:
                self.first = False
                raise KeyboardInterrupt()
            return super().observe(directory, job)

    flow = Workflow("live")
    a = flow.add(
        "a",
        Task(
            python="examples.tasks:slow",
            inputs=[TaskInput("value"), TaskInput("seconds")],
            outputs=[TaskOutput("value")],
        ),
        inputs={"value": 9, "seconds": 0.2},
    )
    flow.expose(value=a.out("value"))
    path = create_run(compile_workflow(flow), tmp_path, allow_local=True)
    backend = InterruptOnce()
    with pytest.raises(KeyboardInterrupt):
        Coordinator(path, backend).run()
    state = Coordinator(path).run()
    assert state["outputs"] == {"value": 9}
    assert len(state["nodes"]["live/a"]["attempts"]) == 1
    for child in backend.children.values():
        child.wait(timeout=5)


def test_cancel_active_work_preserves_finalizer(tmp_path):
    import threading
    import time

    flow = Workflow("cancel")
    work = flow.add(
        "work",
        Task(
            python="examples.tasks:slow",
            inputs=[TaskInput("value"), TaskInput("seconds")],
            outputs=[TaskOutput("value")],
        ),
        inputs={"value": 1, "seconds": 10},
    )
    flow.add(
        "cleanup",
        Task(
            python="examples.tasks:slow",
            inputs=[TaskInput("value"), TaskInput("seconds")],
            outputs=[TaskOutput("value")],
        ),
        inputs={"value": 2, "seconds": 0.2},
        after=(work,),
        trigger="all_terminal",
        finalizer=True,
    )
    path = create_run(compile_workflow(flow), tmp_path, allow_local=True)

    def request():
        deadline = time.monotonic() + 5
        while not list(path.glob("attempts/**/runner.json")) and time.monotonic() < deadline:
            time.sleep(0.01)
        # Let the runner install its handlers after publishing its process identity.
        time.sleep(0.05)
        (path / "cancel.request").touch()

    thread = threading.Thread(target=request)
    thread.start()
    state = Coordinator(path).run()
    thread.join(timeout=6)
    assert state["status"] == "cancelled"
    assert state["nodes"]["cancel/work"]["status"] == "cancelled"
    assert state["nodes"]["cancel/cleanup"]["status"] == "succeeded"


def test_unselected_nested_branch_never_starts_tasks(tmp_path):
    child = Workflow("child")
    child.add("bad", Task(python="examples.tasks:fail"))
    flow = Workflow("branch")
    choice = flow.decision("choose", "examples.tasks:choose", inputs={"value": 0})
    flow.add("unused", child, when=(choice, "large"))
    state, _ = execute(flow, tmp_path)
    assert state["status"] == "succeeded"
    assert state["nodes"]["branch/unused/bad"]["attempts"] == []


@pytest.mark.parametrize("optional,trigger", [(False, "selected_success"), (True, "all_success")])
def test_invalid_conditional_join_rejected(optional, trigger):
    from orchestrator import Select

    flow = Workflow("badjoin")
    choice = flow.decision("choice", "examples.tasks:choose", inputs={"value": 0})
    branch = flow.add("branch", NUMBER, inputs={"value": 1}, when=(choice, "small"))
    value = Select((branch.out("value"),)) if optional else branch.out("value")
    flow.add("join", NUMBER, inputs={"value": value}, trigger=trigger)
    with pytest.raises(ValueError, match="conditional"):
        compile_workflow(flow)


def test_policy_exception_is_durable_failure(tmp_path):
    flow = Workflow("policy")
    decision = flow.decision("broken", "examples.tasks:broken_policy")
    flow.add("blocked", NUMBER, inputs={"value": 1}, after=(decision,))
    state, _ = execute(flow, tmp_path)
    assert state["status"] == "failed"
    assert "ArithmeticError" in state["nodes"]["policy/broken"]["reason"]
    assert state["nodes"]["policy/blocked"]["attempts"] == []


def test_missing_artifact_does_not_publish_success(tmp_path):
    flow = Workflow("missing")
    producer = Task(
        python="examples.tasks:number",
        inputs=[TaskInput("value")],
        outputs=[TaskOutput("value", kind="file")],
    )
    flow.add("artifact", producer, inputs={"value": "does-not-exist.txt"})
    state, _ = execute(flow, tmp_path)
    assert state["status"] == "failed"
    assert state["nodes"]["missing/artifact"]["outputs"] == {}


def test_conditional_join_into_nested_workflow(tmp_path):
    from orchestrator import Select

    child = Workflow("child", inputs=("value",))
    a = child.add("number", NUMBER, inputs={"value": Input("value")})
    child.expose(value=a.out("value"))
    flow = Workflow("nestedjoin")
    decision = flow.decision("choice", "examples.tasks:choose", inputs={"value": 0})
    large = flow.add("large", NUMBER, inputs={"value": 100}, when=(decision, "large"))
    small = flow.add("small", NUMBER, inputs={"value": 1}, when=(decision, "small"))
    joined = flow.add(
        "joined",
        child,
        inputs={"value": Select((large.out("value"), small.out("value")))},
        trigger="selected_success",
    )
    flow.expose(value=joined.out("value"))
    state, _ = execute(flow, tmp_path)
    assert state["status"] == "succeeded"
    assert state["outputs"] == {"value": 1}


def test_failed_draining_work_cannot_be_hidden_by_retry(tmp_path):
    flow = Workflow("drain")
    flow.add("a_work", Task(python="examples.tasks:fail", retry=Retry(max_attempts=3)))
    flow.decision("z_finish", "examples.tasks:finish_empty")
    state, _ = execute(flow, tmp_path)
    assert state["status"] == "failed"
    work = state["nodes"]["drain/a_work"]
    assert work["status"] == "failed"
    assert len(work["attempts"]) == 1
