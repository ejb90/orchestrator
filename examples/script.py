"""Run: orchestrator run examples/script.py --allow-local."""

from orchestrator import Script, Task, TaskOutput, Workflow

workflow = Workflow("script_example")
job = workflow.add(
    "report",
    Task(
        script=Script(body='mkdir -p results\nprintf "%s\\n" "$GREETING" > results/report.txt'),
        env={"GREETING": "Hello from a retained shell script"},
        outputs=[TaskOutput("report", kind="file", source="results/report.txt")],
    ),
)
workflow.expose(report=job.out("report"))
