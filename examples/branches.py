from orchestrator import Select, Task, TaskInput, TaskOutput, Workflow

number = Task(python="examples.tasks:number", inputs=[TaskInput("value")], outputs=[TaskOutput("value")])
workflow = Workflow("branches")
choice = workflow.decision("choose", "examples.tasks:choose", inputs={"value": 12})
large = workflow.add("large", number, inputs={"value": 100}, when=(choice, "large"))
small = workflow.add("small", number, inputs={"value": 1}, when=(choice, "small"))
join = workflow.add(
    "join",
    number,
    inputs={"value": Select((large.out("value"), small.out("value")))},
    trigger="selected_success",
)
workflow.expose(value=join.out("value"))
