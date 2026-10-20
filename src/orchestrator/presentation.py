"""Terminal views of compiled workflows; no execution or graph changes."""

from __future__ import annotations

import json

from rich.table import Table
from rich.text import Text
from rich.tree import Tree


def plan_tree(spec: dict) -> Tree:
    """Show prerequisite branches, collapsing scope barriers and redundant edges."""
    nodes = spec["nodes"]
    ancestors: dict[str, set[str]] = {}
    pending = set(nodes)
    while pending:
        ready = sorted(name for name in pending if set(nodes[name]["deps"]) <= ancestors.keys())
        if not ready:
            raise ValueError("Dependency cycle or missing dependency in visualization")
        for name in ready:
            ancestors[name] = set(nodes[name]["deps"])
            for dependency in nodes[name]["deps"]:
                ancestors[name].update(ancestors[dependency])
        pending.difference_update(ready)

    visible = {name for name, node in nodes.items() if node["kind"] in {"task", "decision"}}
    dependencies = {}
    for name in visible:
        candidates = ancestors[name] & visible
        redundant = set().union(*(ancestors[dependency] for dependency in candidates))
        dependencies[name] = candidates - redundant
    prerequisites = set().union(*dependencies.values())
    root = Tree(Text(spec["root"].removesuffix("/@exit") + " — prerequisites"))
    if not visible:
        root.add(Text("(no task or decision nodes)"))
        return root
    # Iterative traversal handles deep workflows without a recursive renderer builder.
    stack = [(name, root) for name in sorted(visible - prerequisites, reverse=True)]
    expanded: set[str] = set()
    while stack:
        name, parent = stack.pop()
        node = nodes[name]
        label = f"{name} ({node['kind']})"
        if name in expanded:
            parent.add(Text(label + " [shared; see above]"))
            continue
        expanded.add(name)
        if node["finalizer"]:
            label += " [required finalizer]"
        if node["when"]:
            label += f" [when {node['when'][0]} = {node['when'][1]}]"
        if node["trigger"] != "all_success":
            label += f" [trigger: {node['trigger']}]"
        branch = parent.add(Text(label))
        stack.extend((dependency, branch) for dependency in sorted(dependencies[name], reverse=True))
    return root


def plan_table(spec: dict) -> Table:
    """Show every node and edge in deterministic topological order."""
    table = Table(
        "Node",
        "Kind",
        "Depends on",
        "Execution / controls",
        title=Text(f"Workflow: {spec['root'].removesuffix('/@exit')}"),
        caption="Dependencies are prerequisites; row order does not imply serial execution.",
    )
    pending = set(spec["nodes"])
    seen: set[str] = set()
    while pending:
        ready = sorted(name for name in pending if set(spec["nodes"][name]["deps"]) <= seen)
        if not ready:
            raise ValueError("Dependency cycle or missing dependency in visualization")
        for name in ready:
            node = spec["nodes"][name]
            details = []
            if node["kind"] == "task":
                task = node["task"]
                script = task.get("script")
                if script:
                    details.append(f"{script['shell']} / {script['backend']}")
                    if script["slurm"]:
                        details.append("Slurm: " + json.dumps(script["slurm"], sort_keys=True))
                else:
                    details.append(("Python" if task["python"] else "command") + " / local")
                details.append(f"CPUs: {task['resources']['cpus']}")
                if task["resources"]["timeout_seconds"] is not None:
                    details.append(f"Runner timeout: {task['resources']['timeout_seconds']}s")
                if node["finalizer"]:
                    details.append("required finalizer")
            elif node["kind"] == "decision":
                details.append("policy: " + node["policy"])
            else:
                details.append("scope barrier")
                if node["kind"] == "enter":
                    scope = spec["scopes"][name.removesuffix("/@enter")]
                    if scope["max_active"] is not None:
                        details.append(f"max active: {scope['max_active']}")
            if node["when"]:
                details.append(f"when {node['when'][0]} = {node['when'][1]}")
            details.append("trigger: " + node["trigger"])
            table.add_row(
                Text(name),
                Text(node["kind"]),
                Text("\n".join(sorted(node["deps"])) or "—"),
                Text("\n".join(details)),
            )
        seen.update(ready)
        pending.difference_update(ready)
    return table
