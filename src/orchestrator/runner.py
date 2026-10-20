"""Worker-side manifest protocol. Workers never open the coordinator database."""

from __future__ import annotations

import importlib
import json
import os
import signal
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

from .artifacts import collect_outputs, stage_inputs
from .spec import dumps


def atomic_json(path: Path, value: Any) -> None:
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temp.open("w") as stream:
        stream.write(dumps(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def load_callable(reference: str):
    module, name = reference.split(":", 1)
    target = importlib.import_module(module)
    for part in name.split("."):
        target = getattr(target, part)
    if not callable(target):
        raise ValueError(f"Not callable: {reference}")
    return target


def process_identity(pid: int) -> str | None:
    """Linux boot ID + start ticks protect against PID reuse and reboot."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip() + ":" + fields[19]
    except (OSError, IndexError):
        return None


class Interrupted(BaseException):
    pass


class TimedOut(BaseException):
    pass


def main() -> None:
    manifest_path = Path(sys.argv[1]).resolve()
    manifest = json.loads(manifest_path.read_text())
    if os.getpgrp() != os.getpid():
        os.setsid()
    directory = manifest_path.parent
    os.chdir(directory)
    identity = {k: manifest[k] for k in ("run_id", "node_id", "attempt_id")}
    atomic_json(
        directory / "runner.json", dict(pid=os.getpid(), identity=process_identity(os.getpid()), **identity)
    )

    def interrupted(signum, frame):
        raise Interrupted()

    def timed_out(signum, frame):
        raise TimedOut()

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGALRM, timed_out)
    task = manifest["task"]
    timeout = task["resources"]["timeout_seconds"]
    if timeout:
        signal.setitimer(signal.ITIMER_REAL, timeout)
    result = dict(identity, status="failed", category="application", outputs={})
    child = None
    try:
        os.environ.update(task["env"])
        inputs = stage_inputs(manifest["inputs"], task["inputs"], directory)
        staged_path = directory / "staged-inputs.json"
        atomic_json(staged_path, {**manifest, "inputs": inputs})
        os.environ["ORCHESTRATOR_INPUTS"] = str(staged_path)
        os.environ["ORCHESTRATOR_OUTPUTS"] = str(directory / "outputs.json")
        if task["python"]:
            values = load_callable(task["python"])(**inputs)
        else:
            if task.get("script"):
                from .scripts import verified_script

                record = verified_script(directory)
                if record != manifest["execution_script"]:
                    raise ValueError("Script metadata differs from submission intent")
                env = dict(os.environ) if task["script"]["inherit_env"] else {"PATH": os.defpath}
                env.update(
                    ORCHESTRATOR_INPUTS=str(staged_path),
                    ORCHESTRATOR_OUTPUTS=str(directory / "outputs.json"),
                )
                child = subprocess.Popen(
                    [record["interpreter"], str(directory / record["path"]), "--application"], env=env
                )
            else:
                child = subprocess.Popen(task["command"])
            code = child.wait()
            if code != 0:
                raise RuntimeError(f"Command exited with status {code}")
            output_path = directory / "outputs.json"
            values = json.loads(output_path.read_text()) if output_path.exists() else {}
        result.update(status="succeeded", outputs=collect_outputs(values, task["outputs"], directory))
    except (Interrupted, TimedOut) as exc:
        result.update(
            status="cancelled" if isinstance(exc, Interrupted) else "failed",
            category="cancelled" if isinstance(exc, Interrupted) else "timeout",
            error=type(exc).__name__,
        )
        # The runner owns this process group. Kill descendants before publishing a result.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        os.killpg(os.getpgrp(), signal.SIGTERM)
        if child:
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                # Do not claim completion while work may still be running.
                os.killpg(os.getpgrp(), signal.SIGKILL)
    except BaseException as exc:
        traceback.print_exc()
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    atomic_json(directory / "result.json", result)


if __name__ == "__main__":
    main()
