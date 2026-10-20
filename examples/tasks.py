"""Install your own task module for real deployments; these run from the checkout."""

import time
from pathlib import Path


def number(value):
    return {"value": value}


def double(value):
    return {"value": value * 2}


def add(left, right):
    return {"value": left + right}


def increment(value):
    return {"value": value + 1}


def choose(value):
    return {"action": "branch", "branch": "large" if value >= 10 else "small"}


def converge(value, iteration):
    if value >= 4:
        return {"action": "finish", "outputs": {"value": value}, "reason": f"Converged at {iteration}"}
    return {"action": "continue"}


def cleanup():
    Path("cleanup.txt").write_text("Finalizer completed\n")
    return {"report": "cleanup.txt"}


def fail():
    raise RuntimeError("Deliberate demonstration failure")


def slow(value, seconds=1):
    time.sleep(seconds)
    return {"value": value}


def broken_policy():
    raise ArithmeticError("Deliberate policy error")


def finish_empty():
    return {"action": "finish", "outputs": {}, "reason": "Stop ordinary work"}


def summarize_file(source, optional_settings=None):
    text = Path(source["path"]).read_text()
    Path("result.txt").write_text(text.upper())
    return {"lines": len(text.splitlines())}


def nonempty(artifact):
    """Output validators return True to accept a value or artifact."""
    return Path(artifact["path"]).stat().st_size > 0
