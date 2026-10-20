"""Single-owner durable coordinator; all execution passes through an attempt intent."""

from __future__ import annotations

import json
import platform
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from .artifacts import validate_outputs
from .backends import ExecutionBackend, RoutingBackend
from .runner import atomic_json, load_callable
from .spec import digest, validate
from .storage import Store, ownership

TERMINAL = {"succeeded", "failed", "skipped", "cancelled"}


def resolve(value: Any, states: dict) -> Any:
    if isinstance(value, dict):
        if "$ref" in value:
            node = states[value["$ref"]]
            if node["status"] != "succeeded" or value["output"] not in node["outputs"]:
                raise ValueError(f"Unavailable required output: {value}")
            return node["outputs"][value["output"]]
        if "$select" in value:
            for ref in value["$select"]:
                node = states[ref["$ref"]]
                if node["status"] == "succeeded" and ref["output"] in node["outputs"]:
                    return node["outputs"][ref["output"]]
            return resolve(value["default"], states)
        return {k: resolve(v, states) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, states) for v in value]
    return value


def readiness(node: dict, states: dict) -> str:
    """Pure dependency/guard evaluation; scope closure is applied by the caller."""
    dependencies = [states[d]["status"] for d in node["deps"]]
    if any(s not in TERMINAL for s in dependencies):
        return "waiting"
    if node["when"]:
        decision, branch = node["when"]
        if states[decision]["outputs"].get("branch") != branch:
            return "skipped"
    if node["trigger"] == "all_success" and any(s != "succeeded" for s in dependencies):
        return "skipped"
    if node["trigger"] == "selected_success" and any(s in {"failed", "cancelled"} for s in dependencies):
        return "skipped"
    return "ready"


def create_run(spec: dict, root: Path, *, allow_local: bool = False, max_active: int = 4) -> Path:
    validate(spec)
    tasks = [n["task"] for n in spec["nodes"].values() if n["kind"] == "task"]
    if not allow_local and (
        not tasks or any((task.get("script") or {}).get("backend", "local") == "local" for task in tasks)
    ):
        raise ValueError("Local compute requires explicit allow_local=True / --allow-local")
    if platform.system() != "Linux":
        raise ValueError("First-pass local execution requires Linux process identity support")
    if max_active < 1:
        raise ValueError("max_active must be positive")
    run_id = uuid.uuid4().hex
    directory = (root / run_id).resolve()
    directory.mkdir(parents=True)
    state = dict(
        schema_version=1,
        revision=0,
        run_id=run_id,
        status="running",
        spec=spec,
        spec_digest=digest(spec),
        max_active=max_active,
        closing={},
        decisions=[],
        provenance={
            "python": sys.version,
            "executable": sys.executable,
            "platform": platform.platform(),
            "created_at": time.time(),
        },
        nodes={
            node: dict(status="waiting", outputs={}, attempts=[], reason=None, retry_at=0)
            for node in spec["nodes"]
        },
    )
    atomic_json(directory / "spec.json", spec)
    store = Store(directory / "state.sqlite")
    try:
        store.save(state, "run_created", spec_digest=state["spec_digest"])
    finally:
        store.close()
    return directory


class Coordinator:
    def __init__(self, directory: Path, backend: ExecutionBackend | None = None):
        self.directory = directory.resolve()
        if not (self.directory / "state.sqlite").is_file():
            raise ValueError(f"Unknown run: {directory}")
        self.backend = backend or RoutingBackend()
        self.store: Store
        self.state: dict
        self.spec: dict

    def save(self, kind: str, **data) -> None:
        self.store.save(self.state, kind, **data)

    def transition(self, node_id: str, status: str, reason: str | None = None, outputs=None):
        node = self.state["nodes"][node_id]
        node.update(status=status, reason=reason)
        if outputs is not None:
            node["outputs"] = outputs
        self.save("node_transition", node_id=node_id, status=status, reason=reason)

    def scopes(self, scope: str | None):
        while scope:
            yield scope
            scope = self.spec["scopes"][scope]["parent"]

    def cancelled(self) -> bool:
        return bool(self.state.get("cancel_requested"))

    def collect(self) -> None:
        for node_id, state in self.state["nodes"].items():
            if state["status"] != "active":
                continue
            attempt = state["attempts"][-1]
            directory = self.directory / attempt["path"]
            observation = self.backend.observe(directory, attempt.get("job"))
            if observation == "result":
                try:
                    result = json.loads((directory / "result.json").read_text())
                    if any(
                        result.get(k) != v
                        for k, v in {
                            "run_id": self.state["run_id"],
                            "node_id": node_id,
                            "attempt_id": attempt["id"],
                        }.items()
                    ):
                        raise ValueError("Result identity mismatch")
                    if result["status"] not in {"succeeded", "failed", "cancelled"}:
                        raise ValueError("Invalid result status")
                    if result["status"] == "succeeded":
                        result["outputs"] = validate_outputs(
                            result["outputs"], self.spec["nodes"][node_id]["task"]["outputs"], directory
                        )
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    if attempt["status"] != "uncertain":
                        attempt.update(status="uncertain", error=str(exc))
                        self.save("result_rejected", node_id=node_id, error=str(exc))
                    continue
                attempt.update(status=result["status"], result=result, finished_at=time.time())
                definition = self.spec["nodes"][node_id]
                retry = definition["task"]["retry"]
                closing = any(scope in self.state["closing"] for scope in self.scopes(definition["scope"]))
                if (
                    result["status"] == "failed"
                    and len(state["attempts"]) < retry["max_attempts"]
                    and result.get("category") in retry["categories"]
                    and (definition["finalizer"] or not (self.cancelled() or closing))
                ):
                    state["retry_at"] = time.time() + retry["backoff_seconds"]
                    self.transition(node_id, "waiting", "Retry after " + result.get("error", "failure"))
                else:
                    self.transition(node_id, result["status"], result.get("error"), result.get("outputs", {}))
            elif observation == "uncertain":
                # A process can be between Popen and publishing its identity record.
                if time.time() - attempt["created_at"] < 1:
                    continue
                if attempt["status"] != "uncertain":
                    attempt["status"] = "uncertain"
                    self.save("submission_uncertain", node_id=node_id, attempt_id=attempt["id"])
            elif attempt["status"] != "running":
                attempt["status"] = "running"
                self.save("attempt_running", node_id=node_id, attempt_id=attempt["id"])
            if (
                self.cancelled()
                and state["status"] == "active"
                and not self.spec["nodes"][node_id]["finalizer"]
                and not attempt.get("cancel_sent")
                and observation == "running"
            ):
                # Intent precedes the external action; repeating SIGTERM after a crash is safe.
                if not attempt.get("cancel_requested"):
                    attempt["cancel_requested"] = True
                    self.save("attempt_cancel_requested", node_id=node_id)
                if self.backend.cancel(directory, attempt.get("job")):
                    attempt["cancel_sent"] = True
                    self.save("attempt_cancel_sent", node_id=node_id)

    def has_capacity(self, node: dict) -> bool:
        active = [self.spec["nodes"][n] for n, s in self.state["nodes"].items() if s["status"] == "active"]
        if len(active) >= self.state["max_active"]:
            return False
        for scope in self.scopes(node["scope"]):
            limit = self.spec["scopes"][scope]["max_active"]
            if limit and sum(scope in self.scopes(n["scope"]) for n in active) >= limit:
                return False
        return True

    def start(self, node_id: str, node: dict, inputs: dict) -> None:
        state = self.state["nodes"][node_id]
        attempt_id = uuid.uuid4().hex
        relative = str(Path("attempts") / node_id / attempt_id)
        directory = self.directory / relative
        directory.mkdir(parents=True)
        manifest = dict(
            run_id=self.state["run_id"],
            node_id=node_id,
            attempt_id=attempt_id,
            task=node["task"],
            inputs=inputs,
            spec_digest=self.state["spec_digest"],
        )
        from .scripts import render

        script_record = render(directory, manifest)
        if script_record:
            manifest["execution_script"] = script_record
        atomic_json(directory / "input.json", manifest)
        attempt = dict(id=attempt_id, path=relative, status="submission_pending", created_at=time.time())
        if script_record:
            attempt["script"] = script_record
        state["attempts"].append(attempt)
        state["status"] = "active"
        self.save("submission_intent", node_id=node_id, attempt_id=attempt_id)
        try:
            attempt["job"] = self.backend.submit(directory)
            attempt["status"] = "running"
            self.save("submission_acknowledged", node_id=node_id, attempt_id=attempt_id, job=attempt["job"])
        except Exception as exc:
            attempt.update(status="uncertain", error=str(exc))
            self.save("submission_uncertain", node_id=node_id, error=str(exc))

    def decision(self, node_id: str, node: dict, inputs: dict):
        try:
            result = load_callable(node["policy"])(**inputs)
        except Exception as exc:
            self.transition(node_id, "failed", f"Policy error: {type(exc).__name__}: {exc}")
            return
        if not isinstance(result, dict) or result.get("action") not in {
            "continue",
            "branch",
            "finish",
            "fail",
            "cancel",
        }:
            raise ValueError("Policy must return a supported action dictionary")
        action = result["action"]
        if action == "branch" and not isinstance(result.get("branch"), str):
            raise ValueError("Branch decisions need a string branch")
        if action == "finish" and set(result.get("outputs", {})) != set(
            self.spec["scopes"][node["scope"]]["outputs"]
        ):
            raise ValueError("Early completion must supply all workflow outputs")
        # Ensure serialization succeeds before mutating state.
        result = json.loads(json.dumps(result, allow_nan=False))
        self.state["decisions"].append(
            dict(node_id=node_id, revision=self.state["revision"], inputs=inputs, result=result)
        )
        if action in {"finish", "fail"}:
            self.state["closing"][node["scope"]] = result
        elif action == "cancel":
            self.state["cancel_requested"] = True
        self.transition(
            node_id,
            "failed" if action == "fail" else "succeeded",
            result.get("reason"),
            {"branch": result["branch"]} if action == "branch" else {},
        )

    def finish_scope(self, node_id: str, node: dict):
        scope = self.spec["scopes"][node["scope"]]
        states = self.state["nodes"]
        entry_state = states[scope["entry"]]["status"]
        if entry_state != "succeeded":
            self.transition(node_id, entry_state, "Workflow scope was not entered")
            return
        closing = self.state["closing"].get(node["scope"], {})
        if any(states[d]["status"] == "failed" for d in node["deps"]) or closing.get("action") == "fail":
            self.transition(node_id, "failed", "Required child or finalizer failed")
        elif self.cancelled() or any(states[d]["status"] == "cancelled" for d in node["deps"]):
            self.transition(node_id, "cancelled", "Cancellation requested")
        else:
            values = (
                closing["outputs"] if closing.get("action") == "finish" else resolve(scope["outputs"], states)
            )
            self.transition(node_id, "succeeded", closing.get("reason"), values)

    def schedule(self) -> bool:
        changed = False
        for node_id, node in self.spec["nodes"].items():
            state = self.state["nodes"][node_id]
            if state["status"] != "waiting":
                continue
            scope = node["scope"]
            if node["kind"] != "exit":
                # Finalizers run only in scopes that were actually entered.
                if scope and self.state["nodes"][self.spec["scopes"][scope]["entry"]]["status"] in {
                    "skipped",
                    "failed",
                    "cancelled",
                }:
                    self.transition(node_id, "skipped", "Workflow scope was not entered")
                    changed = True
                    continue
                closing = any(s in self.state["closing"] for s in self.scopes(scope))
                if (closing or self.cancelled()) and not node["finalizer"]:
                    self.transition(
                        node_id, "skipped", "Scope closing" if closing else "Run cancellation requested"
                    )
                    changed = True
                    continue
            ready = readiness(node, self.state["nodes"])
            if ready == "waiting":
                continue
            if ready == "skipped":
                self.transition(node_id, "skipped", "Dependency or branch trigger not satisfied")
                changed = True
                continue
            if node["kind"] == "task" and (not self.has_capacity(node) or state["retry_at"] > time.time()):
                continue
            try:
                inputs = resolve(node["inputs"], self.state["nodes"])
                if node["kind"] == "task":
                    self.start(node_id, node, inputs)
                elif node["kind"] == "decision":
                    self.decision(node_id, node, inputs)
                elif node["kind"] == "exit":
                    self.finish_scope(node_id, node)
                else:
                    self.transition(node_id, "succeeded", outputs=inputs)
            except (ValueError, TypeError, KeyError, ImportError, AttributeError, RuntimeError) as exc:
                self.transition(node_id, "failed", str(exc))
            changed = True
        return changed

    def run(self, poll_interval: float = 0.05) -> dict:
        with ownership(self.directory):
            self.store = Store(self.directory / "state.sqlite")
            try:
                self.state = self.store.load()
                self.spec = self.state["spec"]
                validate(self.spec)
                if digest(self.spec) != self.state["spec_digest"]:
                    raise ValueError("Stored specification digest mismatch")
                if self.state["status"] in TERMINAL:
                    return self.state
                self.state["status"] = "running"
                self.save("coordinator_started")
                while True:
                    if (self.directory / "cancel.request").exists() and not self.cancelled():
                        self.state["cancel_requested"] = True
                        self.save("run_cancel_requested")
                    self.collect()
                    changed = self.schedule()
                    states = self.state["nodes"]
                    if all(s["status"] in TERMINAL for s in states.values()):
                        root = states[self.spec["root"]]
                        self.state["status"] = (
                            ("cancelled" if self.cancelled() else "failed")
                            if root["status"] == "skipped"
                            else root["status"]
                        )
                        self.state["outputs"] = root["outputs"]
                        self.save("run_finished", status=self.state["status"])
                        return self.state
                    active = [s["attempts"][-1] for s in states.values() if s["status"] == "active"]
                    if not changed and active and all(a["status"] == "uncertain" for a in active):
                        self.state["status"] = "uncertain"
                        self.save("run_uncertain")
                        return self.state
                    time.sleep(poll_interval)
            finally:
                self.store.close()
