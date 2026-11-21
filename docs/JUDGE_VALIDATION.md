# Judge validation: a real instability we found and fixed

## What we saw (before)

In the first real experiment (`e181b969`, Hermes + gpt-4o-mini, 21 tasks × 5 trials × 2 configs), the
agent gave **the same answer** to `judge-01` ("explain to a 10-year-old why the sky is blue") 7 times:

> The sky looks blue because of a process called scattering. When sunlight enters the Earth's atmosphere,
> it hits tiny particles and spreads out in all directions. Blue light is scattered more than other colors
> because it travels in shorter, smaller waves, making the sky appear blue to our eyes.

Four of the answers matched word for word. The other three differed by a single word ("gets scattered" instead
of "is scattered").

| Trial | Config | Judge verdict |
|-------|--------|---------------|
| e181b969-104 | skills-off | PASS |
| e181b969-188 | skills-off | PASS |
| e181b969-63 | skills-on | PASS |
| e181b969-20 | skills-off | FAIL |
| e181b969-21 | skills-on | FAIL |
| e181b969-105 | skills-on | FAIL |
| e181b969-189 | skills-on | FAIL |

**3 pass / 4 fail for essentially the same input, with the judge at temperature 0.** Every FAIL gave the same
reason: *"the term 'scattering' might be slightly complex for a 10-year-old without further explanation"*.
Yet the answer explains scattering in plain words ("hits tiny particles and spreads out").

### Why this matters
- This task's score was a coin flip, unrelated to the agent. It added noise to both configs, and it could
  create a fake per-task "difference" between them.
- Temperature 0 is **not** deterministic on hosted APIs (batching, mixture-of-experts routing, and GPU
  floating-point order all vary), so "we set temperature 0" does not prove a judge is stable.

### How we caught it, honestly
The original `judge_validation.py` measured only **accuracy against human labels**, with one judge call per
example. It could not detect instability, because each example was judged once. We found the problem by
**inspecting trajectories**: the same final answer appeared under different verdicts in the per-task table.
That gap in the validation is the lesson.

## What we changed (after)

1. **A rubric that leaves less to the judge's own interpretation** (`tasks/judge-01.yaml`):

   | Before | After |
   |--------|-------|
   | 3. The language is simple enough for a 10-year-old (no unexplained jargon like "Rayleigh" without explanation). | 3. The language is simple enough for a 10-year-old. Everyday science words such as "scatter", "scattering", "particles", "atmosphere" or "waves" are allowed, especially when the answer explains them in plain words. Fail this point only for technical terms such as "Rayleigh", "wavelength", "nanometre" or "cross-section" used without a plain explanation. |

   The judge prompt also now says: *"Judge only what the rubric says. Do not add stricter requirements of your own."*

2. **Majority vote** (`worker/graders.py`, `judge_votes: 5` in `models.yaml`): the judge is asked 5 times and
   the majority wins. Every vote is stored, and the grader reasoning starts with the tally
   (`[judge votes: 4 pass / 1 fail]`), so remaining instability is visible in the trial view instead of hidden.
   Cost: 5× judge calls, still under $0.01 per experiment (3 judge tasks).

3. **Validation now measures instability** (`worker/judge_validation.py`): it reports `unstable_items`, the
   labelled examples where repeated calls on the same input disagreed. The exact answer above was added to
   `judge_validation/labels.json` with human label **pass**, so this regression is now part of the test set.

## How to reproduce the before/after numbers

```bash
# "before" behaviour: a single call, run it a few times and compare verdicts
git show 653012a:tasks/judge-01.yaml > /tmp/old-judge-01.yaml   # the old rubric, for reference
python worker/judge_validation.py --votes 1

# "after": majority of 5 with the new rubric
python worker/judge_validation.py --votes 5
```

| Metric | Before (single call, old rubric) | After (5 votes, new rubric) |
|--------|----------------------------------|-----------------------------|
| Same answer in experiment e181b969 | 3 pass / 4 fail | TODO: re-run the experiment |
| Accuracy vs 19 labels | TODO | TODO |
| Cohen's kappa | TODO | TODO |
| Unstable items | not measured | TODO |

Fill the TODOs from the script output. **Acceptance rule:** kappa ≥ 0.8, at most 1 false pass, and 0 unstable
items after the majority vote.

## Interview version (30 seconds)
"In my first real run, the same answer got 3 passes and 4 fails from the judge at temperature 0. The rubric
said 'no jargon' and the judge kept arguing about whether 'scattering' is jargon. My validation only checked
agreement with human labels, one call per example, so it was blind to this. I made the rubric explicit about
what counts as jargon, added a 5-vote majority with every vote stored, added an instability metric to the
validation, and turned the real answer into a labelled regression example."
