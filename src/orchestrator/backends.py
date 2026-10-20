"""Execution mechanism independent of scheduling policy (Linux local backend)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Protocol

from .runner import process_identity
from .scripts import verified_script


class ExecutionBackend(Protocol):
    def submit(self, directory: Path) -> dict: ...
    def observe(self, directory: Path, job: dict | None) -> str: ...
    def cancel(self, directory: Path, job: dict | None) -> bool: ...


class LocalBackend:
    def __init__(self):
        self.children: dict[int, subprocess.Popen] = {}

    def submit(self, directory: Path) -> dict:
        # Preserve the coordinator's import path when switching to the attempt directory.
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            str(Path(p or os.getcwd()).resolve()) for p in [os.getcwd(), *sys.path]
        )
        command = [
            sys.executable,
            "-c",
            "from orchestrator.runner import main; main()",
            str(directory / "input.json"),
        ]
        if (directory / "script.json").exists():
            record = verified_script(directory)
            if record["backend"] != "local":
                raise ValueError("Local backend cannot execute a Slurm script")
            command = record["submission_argv"]
        with (directory / "stdout.log").open("ab") as stdout, (directory / "stderr.log").open("ab") as stderr:
            child = subprocess.Popen(
                command,
                cwd=directory,
                env=env,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
        self.children[child.pid] = child
        return {"pid": child.pid, "identity": process_identity(child.pid)}

    def _job(self, directory: Path, job: dict | None) -> dict | None:
        record = directory / "runner.json"
        return json.loads(record.read_text()) if record.exists() else job

    def observe(self, directory: Path, job: dict | None) -> str:
        for pid, child in list(self.children.items()):
            if child.poll() is not None:
                del self.children[pid]
        if (directory / "result.json").exists():
            return "result"
        job = self._job(directory, job)
        if job and job["identity"] and process_identity(job["pid"]) == job["identity"]:
            return "running"
        return "uncertain"

    def cancel(self, directory: Path, job: dict | None) -> bool:
        job = self._job(directory, job)
        if job and job["identity"] and process_identity(job["pid"]) == job["identity"]:
            try:
                os.killpg(job["pid"], signal.SIGTERM)
            except ProcessLookupError:
                return False
            return True
        return False


class SlurmBackend:
    """Shared-filesystem Slurm adapter; ambiguous submissions are never resubmitted."""

    def submit(self, directory: Path) -> dict:
        record = verified_script(directory)
        if record["backend"] != "slurm":
            raise ValueError("Slurm requires a rendered Slurm script")
        argv = record["submission_argv"]
        env = {k: v for k, v in os.environ.items() if not k.startswith("SBATCH_")}
        result = subprocess.run(argv, env=env, capture_output=True, text=True, check=True, timeout=30)
        job_id = result.stdout.strip()
        # Federated cluster routing requires a separate site contract.
        if not job_id.isdecimal():
            raise ValueError(f"Unrecognized sbatch acknowledgement: {job_id!r}")
        return {"job_id": job_id, "argv": argv, "backend": "slurm"}

    def observe(self, directory: Path, job: dict | None) -> str:
        if (directory / "result.json").exists():
            return "result"
        if not job:
            return "uncertain"
        try:
            result = subprocess.run(
                ["squeue", "--noheader", "--jobs", job["job_id"], "--format=%T"],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return "uncertain"
        states = result.stdout.split()
        if states and all(
            s in {"PENDING", "RUNNING", "CONFIGURING", "COMPLETING", "SUSPENDED"} for s in states
        ):
            return "running"
        # Accounting lag, scheduler failure, missing result, or lost acknowledgement
        # cannot prove managed completion. Preserve the intent for reconciliation.
        return "uncertain"

    def cancel(self, directory: Path, job: dict | None) -> bool:
        if not job:
            return False
        try:
            subprocess.run(["scancel", job["job_id"]], capture_output=True, check=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return False
        return True


class RoutingBackend:
    """Choose persisted task placement, including when resuming a run."""

    def __init__(self):
        self.local = LocalBackend()
        self.slurm = SlurmBackend()

    def select(self, directory: Path) -> ExecutionBackend:
        manifest = json.loads((directory / "input.json").read_text())
        script = manifest["task"].get("script")
        return self.slurm if script and script["backend"] == "slurm" else self.local

    def submit(self, directory: Path) -> dict:
        return self.select(directory).submit(directory)

    def observe(self, directory: Path, job: dict | None) -> str:
        return self.select(directory).observe(directory, job)

    def cancel(self, directory: Path, job: dict | None) -> bool:
        return self.select(directory).cancel(directory, job)
