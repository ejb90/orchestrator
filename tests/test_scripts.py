import json
import shutil
import subprocess
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from orchestrator import (
    Coordinator,
    Retry,
    Script,
    Task,
    TaskInput,
    TaskOutput,
    Workflow,
    compile_workflow,
    create_run,
)
from orchestrator.backends import SlurmBackend
from orchestrator.cli import main
from orchestrator.scripts import render, verified_script


def spec_for(script, **kwargs):
    flow = Workflow("shells")
    flow.add("work", Task(script=script, **kwargs))
    return compile_workflow(flow)


def run_spec(tmp_path, spec):
    path = create_run(spec, tmp_path / "runs", allow_local=True)
    state = Coordinator(path).run()
    attempt = path / state["nodes"]["shells/work"]["attempts"][-1]["path"]
    return state, path, attempt


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_scripts_stage_collect_and_quote(tmp_path, shell):
    if not shutil.which(shell):
        pytest.skip(f"{shell} unavailable")
    source = tmp_path / "input.txt"
    source.write_text("payload")
    value = "spaces ' quotes \\ $HOME $(touch BAD) `touch BAD`\nnew line"
    script = Script(shell=shell, body='cat inputs/data > result.txt\nprintf "%s" "$VALUE" > env.txt')
    state, path, attempt = run_spec(
        tmp_path,
        spec_for(
            script,
            env={"VALUE": value},
            inputs=[TaskInput("data", kind="file", source=str(source))],
            outputs=[TaskOutput("result", kind="file", source="result.txt", destination="collected.txt")],
        ),
    )
    assert state["status"] == "succeeded"
    assert (attempt / "collected.txt").read_text() == "payload"
    assert (attempt / "env.txt").read_text() == value
    assert not (attempt / "BAD").exists()
    record = verified_script(attempt)
    job = (attempt / record["path"]).read_text()
    assert "orchestrator.runner" in job and "cat inputs/data" in job
    assert '"destination": "collected.txt"' in job
    assert record == state["nodes"]["shells/work"]["attempts"][0]["script"]
    result = CliRunner().invoke(main, ["--root", str(path.parent), "script", path.name, "shells/work"])
    assert result.exit_code == 0, result.output
    assert result.output == job


@pytest.mark.parametrize(
    "shell,body",
    [
        ("bash", "false | cat\ntouch SHOULD_NOT_RUN"),
        ("zsh", "false | cat\ntouch SHOULD_NOT_RUN"),
        ("fish", "false; or exit $status\ntouch SHOULD_NOT_RUN"),
    ],
)
def test_failure_and_retry(tmp_path, shell, body):
    if not shutil.which(shell):
        pytest.skip(f"{shell} unavailable")
    state, path, _ = run_spec(tmp_path, spec_for(Script(shell=shell, body=body), retry=Retry(max_attempts=2)))
    assert state["status"] == "failed"
    attempts = state["nodes"]["shells/work"]["attempts"]
    assert len(attempts) == 2
    assert attempts[0]["script"]["sha256"] != attempts[1]["script"]["sha256"]
    assert not list(path.rglob("SHOULD_NOT_RUN"))


def test_source_frozen_at_compile(tmp_path):
    source = tmp_path / "original.sh"
    source.write_text("#!/bin/bash\nprintf original > result.txt\n")
    spec = spec_for(Script(path=str(source)))
    source.write_text("exit 99\n")
    state, _, attempt = run_spec(tmp_path, spec)
    assert state["status"] == "succeeded"
    assert (attempt / "result.txt").read_text() == "original"
    assert (attempt / "source.script").read_text().startswith("#!/bin/bash")
    record = verified_script(attempt)
    (attempt / record["path"]).write_text("exit 0")
    with pytest.raises(ValueError, match="digest"):
        verified_script(attempt)


def test_clean_environment_and_setup(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOULD_BE_ABSENT", "secret")
    state, _, attempt = run_spec(
        tmp_path,
        spec_for(
            Script(
                inherit_env=False,
                setup="export SETUP_VALUE=ready",
                body='printf "%s:%s:%s" "${SHOULD_BE_ABSENT-unset}" "$SETUP_VALUE" "$DECLARED" > env.txt',
            ),
            env={"DECLARED": "explicit"},
        ),
    )
    assert state["status"] == "succeeded"
    assert (attempt / "env.txt").read_text() == "unset:ready:explicit"
    assert "secret" not in (attempt / "job.sh").read_text()


def test_missing_input_prevents_script(tmp_path):
    state, _, attempt = run_spec(
        tmp_path,
        spec_for(
            Script(body="touch BAD"),
            inputs=[TaskInput("missing", kind="file", source=str(tmp_path / "absent"))],
        ),
    )
    assert state["status"] == "failed"
    assert not (attempt / "BAD").exists()


@pytest.mark.parametrize(
    "script",
    [
        Script(body="echo ok", path="x"),
        Script(body="echo ok", shell="sh"),
        Script(body="#SBATCH --mem=1G\necho ok"),
        Script(body="echo ok\n#SBATCH --mem=1G", backend="slurm"),
        Script(body="#SBATCH --wrap=evil", backend="slurm"),
        Script(body="#SBATCH --mem=$MEM", backend="slurm"),
        Script(body="#SBATCH --mem=1G", backend="slurm", slurm={"mem": "2G"}),
        Script(body="echo ok", backend="slurm", slurm={"mem": "1G", "mem-per-cpu": "1G"}),
    ],
)
def test_invalid_scripts(script):
    with pytest.raises(ValueError):
        spec_for(script)


def slurm_attempt(tmp_path):
    spec = spec_for(Script(body="#SBATCH --mem=1G\necho hello", backend="slurm", slurm={"time": "00:10:00"}))
    task = spec["nodes"]["shells/work"]["task"]
    manifest = dict(
        run_id="run", node_id="shells/work", attempt_id="attempt", spec_digest="digest", task=task
    )
    render(tmp_path, manifest)
    return manifest


def test_slurm_submission_exact_file_and_environment(tmp_path, monkeypatch):
    slurm_attempt(tmp_path)
    job = (tmp_path / "job.sh").read_text()
    assert job.index("#SBATCH --mem=1G") < job.index("exec ")
    assert "#SBATCH --time=00:10:00" in job
    monkeypatch.setenv("SBATCH_MEM_PER_NODE", "999G")
    backend = SlurmBackend()
    with patch(
        "orchestrator.backends.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "123\n", "")
    ) as run:
        ack = backend.submit(tmp_path)
        assert run.call_args.args[0] == ["sbatch", "--parsable", str(tmp_path / "job.sh")]
        assert not any(key.startswith("SBATCH_") for key in run.call_args.kwargs["env"])
    with patch(
        "orchestrator.backends.subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, "RUNNING\n", ""),
    ):
        assert backend.observe(tmp_path, ack) == "running"
    with patch(
        "orchestrator.backends.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")
    ):
        assert backend.observe(tmp_path, ack) == "uncertain"
        assert backend.cancel(tmp_path, ack)
    assert backend.observe(tmp_path, None) == "uncertain"
    (tmp_path / "result.json").write_text(json.dumps({}))
    assert backend.observe(tmp_path, ack) == "result"


def test_slurm_only_does_not_require_local_permission(tmp_path):
    create_run(spec_for(Script(body="true", backend="slurm")), tmp_path)


def test_preview(tmp_path):
    definition = tmp_path / "workflow.py"
    definition.write_text(
        "from orchestrator import *\nworkflow = Workflow('preview')\nworkflow.add('work', Task(script=Script(body='echo hello')))\n"
    )
    result = CliRunner().invoke(main, ["render", str(definition), "--output", str(tmp_path / "preview")])
    assert result.exit_code == 0, result.output
    assert "echo hello" in (tmp_path / "preview/preview/work/job.sh").read_text()
    assert not list(tmp_path.rglob("state.sqlite"))


def test_script_timeout_uses_managed_result(tmp_path):
    from orchestrator import Resources

    state, _, attempt = run_spec(
        tmp_path,
        spec_for(Script(body="sleep 20\ntouch SHOULD_NOT_RUN"), resources=Resources(timeout_seconds=0.1)),
    )
    assert state["status"] == "failed"
    result = json.loads((attempt / "result.json").read_text())
    assert result["category"] == "timeout"
    assert not (attempt / "SHOULD_NOT_RUN").exists()


def test_slurm_scheduler_errors_remain_uncertain(tmp_path):
    slurm_attempt(tmp_path)
    backend = SlurmBackend()
    with patch("orchestrator.backends.subprocess.run", side_effect=subprocess.TimeoutExpired("squeue", 30)):
        assert backend.observe(tmp_path, {"job_id": "123"}) == "uncertain"
        assert not backend.cancel(tmp_path, {"job_id": "123"})
    with patch(
        "orchestrator.backends.subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, "123;cluster\n", ""),
    ):
        with pytest.raises(ValueError, match="acknowledgement"):
            backend.submit(tmp_path)


def test_original_source_bytes_preserved(tmp_path):
    source = tmp_path / "windows.sh"
    content = b"#!/bin/bash\r\nprintf hello\r\n"
    source.write_bytes(content)
    spec = spec_for(Script(path=str(source)))
    task = spec["nodes"]["shells/work"]["task"]
    target = tmp_path / "preview"
    target.mkdir()
    render(
        target,
        dict(run_id="preview", node_id="shells/work", attempt_id="preview", spec_digest="x", task=task),
    )
    assert (target / "source.script").read_bytes() == content
    verified_script(target)
