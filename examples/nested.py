from orchestrator import Input, Task, TaskInput, TaskOutput, Workflow

number = Task(python="examples.tasks:number", inputs=[TaskInput("value")], outputs=[TaskOutput("value")])
double = Task(python="examples.tasks:double", inputs=[TaskInput("value")], outputs=[TaskOutput("value")])
add = Task(
    python="examples.tasks:add", inputs=[TaskInput("left"), TaskInput("right")], outputs=[TaskOutput("value")]
)
child = Workflow("transform", inputs=("value",), max_active=1)
a = child.add("first", double, inputs={"value": Input("value")})
b = child.add("second", double, inputs={"value": a.out("value")})
child.expose(value=b.out("value"))

workflow = Workflow("nested", inputs=("start",))
seed = workflow.add("seed", number, inputs={"value": Input("start")})
left = workflow.add("left", child, inputs={"value": seed.out("value")})
right = workflow.add("right", child, inputs={"value": seed.out("value")})
join = workflow.add("join", add, inputs={"left": left.out("value"), "right": right.out("value")})
workflow.expose(value=join.out("value"))
