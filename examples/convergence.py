from orchestrator import Handle, Task, TaskInput, TaskOutput, Workflow, iterate

step = Task(python="examples.tasks:increment", inputs=[TaskInput("value")], outputs=[TaskOutput("value")])
optimization = iterate("optimization", step, initial=0, max_iterations=6, policy="examples.tasks:converge")
optimization.add(
    "cleanup",
    Task(python="examples.tasks:cleanup", outputs=[TaskOutput("report", kind="file")]),
    after=tuple(Handle(n.id) for n in optimization.nodes),
    trigger="all_terminal",
    finalizer=True,
)
workflow = Workflow("experiment")
result = workflow.add("optimize", optimization)
report = workflow.add(
    "report",
    Task(python="examples.tasks:double", inputs=[TaskInput("value")], outputs=[TaskOutput("value")]),
    inputs={"value": result.out("value")},
)
workflow.expose(value=report.out("value"))
