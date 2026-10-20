import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from orchestrator.cli import load, main

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
EXPECTED_REPORT = (
    "Temperature report\nAccepted readings: 5\nMean temperature: 20.6 C\nRange: 18.5 to 23.0 C\n"
)


@pytest.mark.parametrize("suffix", ["yaml", "py"])
def test_sensor_report_quickstart(tmp_path, monkeypatch, suffix):
    monkeypatch.chdir(tmp_path)
    source = str(EXAMPLES / f"sensor_report.{suffix}")
    runner = CliRunner()
    root = tmp_path / "runs"
    preview = runner.invoke(main, ["plan", source, "--tree"])
    assert preview.exit_code == 0, preview.output
    assert "sensor_report/report" in preview.output and "sensor_report/clean" in preview.output
    assert not root.exists()
    result = runner.invoke(main, ["--root", str(root), "run", source, "--allow-local", "--json"])
    assert result.exit_code == 0, result.output
    state = json.loads(result.stdout)
    assert state["status"] == "succeeded"
    clean = Path(state["outputs"]["clean_csv"]["path"])
    report = Path(state["outputs"]["report"]["path"])
    assert report.read_text() == EXPECTED_REPORT
    assert clean.read_text().splitlines() == [
        "station,temperature_c,status",
        "north,18.5,ok",
        "north,19.5,ok",
        "south,22.0,ok",
        "north,20.0,ok",
        "south,23.0,ok",
    ]
    assert clean.parent.parent != report.parent.parent
    run_id = state["run_id"]
    logs = runner.invoke(main, ["--root", str(root), "logs", run_id, "sensor_report/report"])
    assert logs.exit_code == 0 and logs.stdout == EXPECTED_REPORT
    script = runner.invoke(main, ["--root", str(root), "script", run_id, "sensor_report/report"])
    assert script.exit_code == 0 and '"inputs/clean.csv"' in script.stdout
    assert "awk -F," in script.stdout
    resumed = runner.invoke(main, ["--root", str(root), "resume", run_id, "--json"])
    assert resumed.exit_code == 0, resumed.output
    assert all(
        len(node["attempts"]) == 1
        for node in json.loads(resumed.stdout)["nodes"].values()
        if node["attempts"]
    )


def test_sensor_report_authoring_formats_agree():
    assert load(str(EXAMPLES / "sensor_report.yaml"), "{}") == load(str(EXAMPLES / "sensor_report.py"), "{}")


def test_log_summary_quickstart(tmp_path):
    result = CliRunner().invoke(
        main, ["--root", str(tmp_path), "run", str(EXAMPLES / "log_summary.yaml"), "--allow-local", "--json"]
    )
    assert result.exit_code == 0, result.output
    state = json.loads(result.stdout)
    assert state["status"] == "succeeded"
    assert Path(state["outputs"]["report"]["path"]).read_text() == (
        "Application log summary\nEntries: 6\nWarnings: 1\nErrors: 2\n"
    )
