"""Clean sensor readings and generate a temperature report using retained Bash scripts.

Run from the repository root:
    uv run orchestrator run examples/sensor_report.py --allow-local
"""

from pathlib import Path

from orchestrator import Script, Task, TaskInput, TaskOutput, Workflow

EXAMPLES = Path(__file__).resolve().parent

clean_readings = Task(
    script=Script(path=str(EXAMPLES / "jobs/clean_readings.sh")),
    inputs=[
        TaskInput(
            "readings",
            kind="file",
            source=EXAMPLES / "data/sensor_readings.csv",
            destination="inputs/readings.csv",
        )
    ],
    outputs=[TaskOutput("readings", kind="file", source="results/clean.csv")],
)
temperature_report = Task(
    script=Script(path=str(EXAMPLES / "jobs/temperature_report.sh")),
    inputs=[TaskInput("readings", kind="file", destination="inputs/clean.csv")],
    outputs=[TaskOutput("summary", kind="file", source="results/report.txt")],
)

workflow = Workflow("sensor_report")
clean = workflow.add("clean", clean_readings)
report = workflow.add("report", temperature_report, inputs={"readings": clean.out("readings")})
workflow.expose(clean_csv=clean.out("readings"), report=report.out("summary"))
