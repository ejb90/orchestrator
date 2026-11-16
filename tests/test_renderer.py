from __future__ import annotations

import unittest

from orchestrator.models import Workflow
from orchestrator.renderer import RenderTarget, ScriptRenderer


class RendererTests(unittest.TestCase):
    def test_renders_serial_workflow_and_task_mediation(self) -> None:
        workflow = Workflow.from_mapping(
            {
                "name": "Example",
                "mode": "serial",
                "tasks": [
                    {"name": "First", "command": "echo first"},
                    {"name": "Second", "command": "echo second"},
                ],
            }
        )

        rendered = {
            str(file.relative_path): file.content
            for file in ScriptRenderer().render(workflow)
        }

        self.assertIn("example/run.sh", rendered)
        self.assertIn("example/01-first.sh", rendered)
        self.assertIn(
            "orchestrator mediate --workflow Example --task First",
            rendered["example/01-first.sh"],
        )
        self.assertIn("should_continue || exit 1", rendered["example/run.sh"])

    def test_renders_nested_parallel_workflow(self) -> None:
        workflow = Workflow.from_mapping(
            {
                "name": "Outer",
                "mode": "parallel",
                "tasks": [
                    {
                        "name": "Inner Task",
                        "workflow": {
                            "name": "Inner",
                            "tasks": [{"name": "Leaf", "command": "date"}],
                        },
                    }
                ],
            }
        )

        rendered = {
            str(file.relative_path): file.content
            for file in ScriptRenderer().render(workflow)
        }

        self.assertIn("outer/01-inner-task/inner/run.sh", rendered)
        self.assertIn(
            'STATE_DIR="$STATE_DIR" bash "$SCRIPT_DIR"/01-inner-task/inner/run.sh & pid_1=$!',
            rendered["outer/run.sh"],
        )

    def test_slurm_target_adds_sbatch_directives(self) -> None:
        workflow = Workflow.from_mapping(
            {
                "name": "Cluster",
                "tasks": [
                    {
                        "name": "Compute",
                        "command": "python job.py",
                        "slurm": {"time": "00:10:00", "cpus-per-task": "4"},
                    }
                ],
            }
        )

        rendered = {
            str(file.relative_path): file.content
            for file in ScriptRenderer(RenderTarget.SLURM).render(workflow)
        }

        self.assertIn("#SBATCH --job-name=compute", rendered["cluster/01-compute.sh"])
        self.assertIn("#SBATCH --time=00:10:00", rendered["cluster/01-compute.sh"])
        self.assertIn(
            'STATE_DIR="$STATE_DIR" sbatch --wait "$SCRIPT_DIR"/01-compute.sh',
            rendered["cluster/run.sh"],
        )


if __name__ == "__main__":
    unittest.main()
