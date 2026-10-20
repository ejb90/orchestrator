from orchestrator import Task, TaskInput, TaskOutput, Workflow

number = Task(python="examples.tasks:number", inputs=[TaskInput("value")], outputs=[TaskOutput("value")])
double = Task(python="examples.tasks:double", inputs=[TaskInput("value")], outputs=[TaskOutput("value")])
add = Task(
    python="examples.tasks:add", inputs=[TaskInput("left"), TaskInput("right")], outputs=[TaskOutput("value")]
)

workflow = Workflow("diamond")
seed = workflow.add("seed", number, inputs={"value": 3})
left = workflow.add("left", double, inputs={"value": seed.out("value")})
right = workflow.add("right", double, inputs={"value": seed.out("value")})
join = workflow.add("join", add, inputs={"left": left.out("value"), "right": right.out("value")})
workflow.expose(value=join.out("value"))
