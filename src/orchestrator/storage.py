"""Single-host SQLite snapshots plus transactional, append-only events."""

from __future__ import annotations

import fcntl
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .spec import dumps


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.connection = sqlite3.connect(path, timeout=10)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise ValueError(f"Unsupported database schema {version}")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS snapshot (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                revision INTEGER PRIMARY KEY, timestamp REAL NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL);
            PRAGMA user_version=1;
        """)

    def close(self):
        self.connection.close()

    def load(self) -> dict:
        row = self.connection.execute("SELECT data FROM snapshot WHERE id=1").fetchone()
        if row is None:
            raise ValueError("Run database has no snapshot")
        return json.loads(row[0])

    def save(self, state: dict, kind: str, **data) -> None:
        previous = state["revision"]
        with self.connection:
            row = self.connection.execute("SELECT data FROM snapshot WHERE id=1").fetchone()
            if row and json.loads(row[0])["revision"] != previous:
                raise RuntimeError("Stale coordinator revision")
            state["revision"] += 1
            self.connection.execute("INSERT OR REPLACE INTO snapshot VALUES (1, ?)", (dumps(state),))
            self.connection.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?)", (state["revision"], time.time(), kind, dumps(data))
            )

    def events(self) -> list[dict]:
        return [
            dict(revision=r, timestamp=t, kind=k, data=json.loads(d))
            for r, t, k, d in self.connection.execute("SELECT * FROM events ORDER BY revision")
        ]


@contextmanager
def ownership(directory: Path):
    with (directory / "coordinator.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another coordinator owns this run") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
