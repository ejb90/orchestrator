import sys

from orchestrator import Task, TaskInput, TaskOutput, Workflow

workflow = Workflow("command")
write = workflow.add(
    "write",
    Task(
        command=(
            sys.executable,
            "-c",
            "import json, os; from pathlib import Path; "
            "m=json.loads(Path(os.environ['ORCHESTRATOR_INPUTS']).read_text()); "
            "Path('answer.txt').write_text(str(m['inputs']['value'])); "
            "Path(os.environ['ORCHESTRATOR_OUTPUTS']).write_text(json.dumps({'answer':'answer.txt'}))",
        ),
        inputs=[TaskInput("value")],
        outputs=[TaskOutput("answer", kind="file")],
    ),
    inputs={"value": 42},
)
workflow.expose(answer=write.out("answer"))
