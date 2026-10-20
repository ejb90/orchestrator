"""Copy an input, tolerate a missing optional input, collect and validate a file."""

from pathlib import Path

from orchestrator import Task, TaskInput, TaskOutput, Workflow

summarize = Task(
    python="examples.tasks:summarize_file",
    inputs=[
        TaskInput(
            "source",
            kind="file",
            source=str(Path(__file__).parent / "data/seed.txt"),
            destination="work/seed.txt",
            mode="copy",
        ),
        TaskInput("optional_settings", kind="file", essential=False),
    ],
    outputs=[
        TaskOutput(
            "report",
            kind="file",
            source="result.txt",
            destination="published/report.txt",
            validator="examples.tasks:nonempty",
        ),
        TaskOutput("lines"),
    ],
)
workflow = Workflow("staging")
summary = workflow.add("summarize", summarize)
workflow.expose(report=summary.out("report"), lines=summary.out("lines"))
