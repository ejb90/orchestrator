"""Capture shell sources and render inspectable, immutable attempt programs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import sys
from pathlib import Path

# Deliberately bounded: these settings do not change the managed result protocol.
SLURM_OPTIONS = {
    "partition",
    "account",
    "qos",
    "nodes",
    "ntasks",
    "ntasks-per-node",
    "cpus-per-task",
    "mem",
    "mem-per-cpu",
    "time",
    "gpus",
    "gres",
    "constraint",
    "reservation",
    "exclusive",
}
EXTENSIONS = {"bash": "sh", "zsh": "zsh", "fish": "fish"}


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def capture(script: dict) -> dict:
    script = dict(script)
    if script["shell"] not in EXTENSIONS:
        raise ValueError("Supported shells: bash, zsh, fish")
    if (script["body"] is None) == (script["path"] is None):
        raise ValueError("Script requires exactly one of body or path")
    source = Path(script["path"]).resolve() if script["path"] is not None else None
    original = source.read_bytes().decode("utf-8") if source else script["body"]
    if not isinstance(original, str):
        raise ValueError("Script body must be text")
    headers: dict[str, str] = {}
    body = []
    executable_seen = False
    for index, line in enumerate(original.splitlines(keepends=True)):
        stripped = line.strip()
        if index == 0 and stripped.startswith("#!"):
            tokens = shlex.split(stripped[2:])
            expected = ["/usr/bin/env", script["shell"]]
            matching_absolute = (
                len(tokens) == 1
                and Path(tokens[0]).is_absolute()
                and Path(tokens[0]).name == script["shell"]
                and (script["interpreter"] is None or script["interpreter"] == tokens[0])
            )
            if tokens != expected and not matching_absolute:
                raise ValueError("Source shebang must match the declared shell/interpreter (no options)")
            if matching_absolute:
                script["interpreter"] = tokens[0]
            continue
        if stripped.startswith("#SBATCH"):
            if executable_seen:
                raise ValueError("SBATCH directives must precede executable statements")
            tokens = shlex.split(stripped[len("#SBATCH") :].strip())
            if len(tokens) == 1 and tokens[0].startswith("--"):
                key, _, value = tokens[0][2:].partition("=")
            elif len(tokens) == 2 and tokens[0].startswith("--"):
                key, value = tokens[0][2:], tokens[1]
            else:
                raise ValueError("Use one long SBATCH option per line")
            if key in headers:
                raise ValueError(f"Duplicate SBATCH option: {key}")
            headers[key] = value
            continue
        if stripped and not stripped.startswith("#"):
            executable_seen = True
        body.append(line)
    if headers.keys() & script["slurm"].keys():
        raise ValueError("Imported and structured Slurm settings must not overlap")
    script.update(
        body="".join(body),
        path=str(source) if source else None,
        original=original,
        source_digest=sha256(original),
        slurm={**headers, **script["slurm"]},
    )
    interpreter = script["interpreter"] or shutil.which(script["shell"])
    if not interpreter or not Path(interpreter).is_absolute():
        raise ValueError("Provide an absolute interpreter path, or install the selected shell")
    script["interpreter"] = interpreter
    validate_script(script)
    return script


def validate_script(script: dict) -> None:
    if script["shell"] not in EXTENSIONS or script["backend"] not in {"local", "slurm"}:
        raise ValueError("Supported shells: bash, zsh, fish; backends: local, slurm")
    if not isinstance(script["inherit_env"], bool):
        raise ValueError("inherit_env must be Boolean")
    if any(not isinstance(script[k], str) or "\x00" in script[k] for k in ("body", "setup", "original")):
        raise ValueError("Script source and setup must be text without NUL bytes")
    if sha256(script["original"]) != script["source_digest"]:
        raise ValueError("Script source digest mismatch")
    interpreter = script["interpreter"]
    if not Path(interpreter).is_absolute() or re.search(r"\s", interpreter):
        raise ValueError("Interpreter must be an absolute path without whitespace")
    if script["backend"] == "local" and script["slurm"]:
        raise ValueError("Slurm headers/settings require backend='slurm'")
    for key, value in script["slurm"].items():
        if key not in SLURM_OPTIONS or not isinstance(value, str) or re.search(r"[\s$`\x00]", value):
            raise ValueError(f"Unsupported or non-static Slurm option: {key}")
        if not value and key != "exclusive":
            raise ValueError(f"Slurm option requires a value: {key}")
    if "mem" in script["slurm"] and "mem-per-cpu" in script["slurm"]:
        raise ValueError("Choose mem or mem-per-cpu")
    if any(line.lstrip().startswith("#SBATCH") for line in script["setup"].splitlines()):
        raise ValueError("Put scheduler directives in slurm settings, not setup")


def quote(value: str, shell: str) -> str:
    if shell == "fish":
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    return shlex.quote(value)


def render(directory: Path, manifest: dict) -> dict | None:
    """Write before the durable submission intent; never reread the original source."""
    task = manifest["task"]
    script = task.get("script")
    if script is None:
        return None
    validate_script(script)
    shell = script["shell"]
    path = directory / ("job." + EXTENSIONS[shell])

    def q(text):
        return quote(str(text), shell)

    lines = ["#!" + script["interpreter"]]
    if script["backend"] == "slurm":
        # Paths must be literal for Slurm, which does not perform shell expansion.
        if re.search(r"[\s%\x00]", str(directory)):
            raise ValueError("Slurm attempt paths cannot contain whitespace or percent tokens")
        settings = dict(script["slurm"])
        settings.setdefault("cpus-per-task", str(task["resources"]["cpus"]))
        settings.update(
            {
                "job-name": "orch-" + manifest["attempt_id"],
                "comment": "orchestrator:" + manifest["attempt_id"],
                "chdir": str(directory),
                "output": str(directory / "stdout.log"),
                "error": str(directory / "stderr.log"),
                "export": "ALL",
            }
        )
        lines.extend(f"#SBATCH --{key}" + (f"={value}" if value else "") for key, value in settings.items())
    lines += [
        "# Generated by orchestrator 0.1.0; managed protocol 1.",
        "# " + json.dumps({k: manifest[k] for k in ("run_id", "node_id", "attempt_id", "spec_digest")}),
        "# Backend: " + script["backend"] + "; source SHA256: " + script["source_digest"],
        "# Environment: "
        + ("inherited" if script["inherit_env"] else "clean (PATH=/bin:/usr/bin)")
        + "; declared task variables below; ambient credentials are not captured.",
        "# Runtime timeout (seconds): " + str(task["resources"]["timeout_seconds"]),
        "# Inputs: " + json.dumps(task["inputs"], sort_keys=True),
        "# Outputs: " + json.dumps(task["outputs"], sort_keys=True),
        "# Lifecycle: runner stages inputs once, executes this file with --application,",
        "# validates/collects outputs and atomically publishes result.json; it handles timeout/signals.",
        "# Requires the recorded Python interpreter, orchestrator and application dependencies.",
    ]
    pythonpath = os.pathsep.join(str(Path(p or os.getcwd()).resolve()) for p in [os.getcwd(), *sys.path])
    runner = " ".join(
        q(x)
        for x in (
            "/usr/bin/env",
            "PYTHONPATH=" + pythonpath,
            sys.executable,
            "-c",
            "from orchestrator.runner import main; main()",
            str(directory / "input.json"),
        )
    )
    if shell == "fish":
        lines += ["if not contains -- --application $argv", "    exec " + runner, "end"]
    else:
        lines += ['if [ "${1-}" != "--application" ]; then', "    exec " + runner, "fi", "set -euo pipefail"]
    lines += ["cd " + q(directory) + ("; or exit $status" if shell == "fish" else "")]
    for key, value in sorted(task["env"].items()):
        lines.append(f"set -gx {key} {q(value)}" if shell == "fish" else f"export {key}={q(value)}")
    lines += [
        "# Environment setup (authored in this shell)",
        script["setup"],
        "# Application commands",
        script["body"],
    ]
    text = "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8")
    path.chmod(0o700)
    (directory / "source.script").write_text(script["original"], encoding="utf-8")
    record = {
        "path": path.name,
        "sha256": sha256(text),
        "interpreter": script["interpreter"],
        "backend": script["backend"],
        "source_digest": script["source_digest"],
        "submission_argv": (
            ["sbatch", "--parsable", str(path)]
            if script["backend"] == "slurm"
            else [script["interpreter"], str(path)]
        ),
    }
    (directory / "script.json").write_text(json.dumps(record, indent=2) + "\n")
    for persisted in (path, directory / "source.script", directory / "script.json"):
        with persisted.open("rb") as stream:
            os.fsync(stream.fileno())
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return record


def verified_script(directory: Path) -> dict:
    record = json.loads((directory / "script.json").read_text())
    if sha256((directory / record["path"]).read_bytes().decode("utf-8")) != record["sha256"]:
        raise ValueError("Retained execution script digest mismatch")
    manifest_path = directory / "input.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if record != manifest.get("execution_script"):
            raise ValueError("Script metadata differs from submission intent")
    return record
