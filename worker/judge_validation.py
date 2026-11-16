"""Check the LLM judge against hand-labelled examples.

Usage: python worker/judge_validation.py      (needs the judge model's API key)
Writes judge_validation/report.json with accuracy, Cohen's kappa and every disagreement.
"""
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT  # noqa: E402
from graders import judge  # noqa: E402


def cohens_kappa(a: list, b: list) -> float:
    """Agreement corrected for chance. 1 = perfect, 0 = no better than chance."""
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b)) / n
    p_a, p_b = sum(a) / n, sum(b) / n
    expected = p_a * p_b + (1 - p_a) * (1 - p_b)
    return 1.0 if expected == 1 else (observed - expected) / (1 - expected)


def main():
    items = json.loads((ROOT / "judge_validation/labels.json").read_text())["items"]
    human, model, disagreements = [], [], []
    for item in items:
        task = yaml.safe_load((ROOT / "tasks" / f"{item['task_id']}.yaml").read_text())
        res = judge(task["prompt"], item["answer"], task["grader"]["rubric"])
        human.append(item["human_label"] == "pass")
        model.append(res["passed"])
        if human[-1] != model[-1]:
            disagreements.append({**item, "judge_reasoning": res["reasoning"]})
        print(f"{item['task_id']:9} human={item['human_label']:4} judge={'pass' if res['passed'] else 'fail'}  {item['note']}")

    report = {
        "n": len(items),
        "accuracy": sum(h == m for h, m in zip(human, model)) / len(items),
        "cohens_kappa": cohens_kappa(human, model),
        "false_pass": sum(m and not h for h, m in zip(human, model)),  # judge too lenient
        "false_fail": sum(h and not m for h, m in zip(human, model)),  # judge too strict
        "disagreements": disagreements,
    }
    (ROOT / "judge_validation/report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "disagreements"}, indent=2))


if __name__ == "__main__":
    main()
