"""Run one prompt (or one task) from the terminal and print the trajectory. For debugging and demos.

  python worker/cli.py --harness hermes --model gpt-4o-mini "What is 2+2?"
  python worker/cli.py --harness baseline --skills acme-billing --task skill-01
  python worker/cli.py --harness hermes --memory --task mem-01 --keep   # keep the trial folder to inspect

--keep leaves the trial's folder on disk: workspace/ (files the agent wrote) and home/ (for Hermes:
home/hermes/memories/MEMORY.md, home/hermes/skills/... including any skill Hermes edited).
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, get_model  # noqa: E402
from graders import grade  # noqa: E402
from run_trial import agent_view  # noqa: E402
from harnesses import get_harness  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("prompt", nargs="?", help="prompt to send (or use --task)")
    p.add_argument("--task", help="task id from tasks/, e.g. skill-01 (also grades the answer)")
    p.add_argument("--harness", default="hermes")
    p.add_argument("--model", default="gemma-4-31b-it-free")
    p.add_argument("--skills", default="", help="comma-separated, e.g. acme-billing,acme-style")
    p.add_argument("--tools", default="file", help="comma-separated: file,terminal")
    p.add_argument("--memory", action="store_true")
    p.add_argument("--max-steps", type=int, default=10)
    p.add_argument("--keep", action="store_true", help="keep the trial folder and print its path")
    a = p.parse_args()

    task = (yaml.safe_load((ROOT / "tasks" / f"{a.task}.yaml").read_text()) if a.task
            else {"id": "cli", "prompt": a.prompt, "grader": {"type": "none"}})
    config = {"harness": a.harness, "model": a.model, "skills": [s for s in a.skills.split(",") if s],
              "tools": [t for t in a.tools.split(",") if t], "memory": a.memory, "temperature": 0,
              "max_steps": a.max_steps, "max_tokens": 1024}

    root = Path(tempfile.mkdtemp(prefix="cli-trial-"))
    workspace, home = root / "workspace", root / "home"
    workspace.mkdir()
    home.mkdir()
    for name, content in task.get("setup_files", {}).items():
        (workspace / name).write_text(content)

    model = get_model(a.model)
    trace = get_harness(a.harness).run(agent_view(task), config, model, workspace, home)

    for e in trace.events:
        detail = e.get("content") or e.get("arguments") or e.get("output") or e.get("stop_reason") or ""
        name = f" {e['name']}" if e.get("name") else ""
        print(f"[{e['type']}{name}] {str(detail)[:300]}")
    print(f"\nFINAL ANSWER: {trace.final_answer}")
    print(f"stop={trace.stop_reason} tokens={trace.input_tokens}+{trace.output_tokens} "
          f"cost=${model.cost(trace.input_tokens, trace.output_tokens):.5f}")
    if a.task:
        print("GRADE:", json.dumps(grade(task, trace, workspace)))
    if a.keep:
        print(f"Trial folder kept at: {root}")
    else:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
