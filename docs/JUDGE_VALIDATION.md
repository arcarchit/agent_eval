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

## Validation results (after the changes)

**Acceptance rule:** kappa ≥ 0.8, at most 1 false pass, and 0 unstable items after the majority vote.
How kappa is computed, with this table's first row worked through: [STATISTICS.md](STATISTICS.md#judge-agreement-cohens-kappa).

Run on 2026-11-23 with the new rubric, 19 hand-labelled answers (`judge_validation/labels.json`), two judge
candidates, 1 and 5 votes. Full reports, including every disagreement and the judge's reasoning:
`judge_validation/results/report_<judge>_v<votes>.json`.

| Judge | Votes | Accuracy | Cohen's kappa | False pass | False fail | Unstable items | Meets rule? |
|-------|-------|----------|---------------|------------|------------|----------------|-------------|
| gpt-4o-mini | 1 | 84.2% (16/19) | 0.68 | 2 | 1 | 0 | no |
| gpt-4o-mini | 5 | 84.2% (16/19) | 0.68 | 2 | 1 | 1 | no |
| qwen3.5-9b | 1 | 78.9% (15/19) | 0.54 | 0 | 4 | 0 | no |
| qwen3.5-9b | 5 | 57.9% (11/19) | 0.04 | 1 | 7 | 8 | no |

**No judge meets the acceptance rule, so the 3 judge-graded tasks are not yet trustworthy.** What the
disagreements show:

1. **gpt-4o-mini misses banned words (2 false passes, `skill-05`).** Both answers contain a word the rubric
   bans ("unfortunately", "sorry"). The judge says, 5 votes out of 5, *"the words 'unfortunately' and 'sorry'
   do not appear anywhere"*. This is a **consistent** error, so majority voting cannot fix it: voting only
   removes random disagreement. Lesson: literal checks (a word is present, the first line is exactly X)
   belong in a deterministic grader, not a judge.
2. **The rubric fix for `judge-01` did not hold for gpt-4o-mini.** The real answer from e181b969, labelled
   pass, now gets 1 pass / 4 fail (before: 3 pass / 4 fail). The judge still calls "scattering" jargon, even
   though the new rubric explicitly allows that word. The vote made the verdict *more consistent*, and
   consistently wrong.
3. **qwen3.5-9b is unusable as configured.** It is a "thinking" model: most replies start with
   "Thinking Process:" or are empty instead of the requested JSON. The grader counts unparseable output as a
   fail vote, so its false fails and its 8 unstable items mostly measure parse failures, not judgement.
   Where it did return JSON, it also missed a banned word.

### Next steps (not done yet)
- Split `skill-05`: a regex/exact grader for the literal rules (greeting line, banned words, sign-off), and
  the judge only for tone. That removes both gpt-4o-mini false passes.
- Count an unparseable judge reply as *no vote* (and error the trial if every vote is unparseable), instead
  of a fail vote.
- Rework `judge-01` (e.g. give the judge the list of allowed words as a check it must apply first), then
  re-run this validation.
- Until then, report results with and without the 3 judge tasks.

### Reproduce
```bash
python worker/judge_validation.py --votes 1   # uses judge_model from models.yaml
python worker/judge_validation.py --votes 5
```

## Interview version (30 seconds)
"In my first real run, the same answer got 3 passes and 4 fails from the judge at temperature 0. The rubric
said 'no jargon' and the judge kept arguing about whether 'scattering' is jargon. My validation only checked
agreement with human labels, one call per example, so it was blind to this. I made the rubric explicit about
what counts as jargon, added a 5-vote majority with every vote stored, added an instability metric to the
validation, and turned the real answer into a labelled regression example. Then the validation itself told me
the fix was not enough: kappa 0.68, and the judge said a banned word was absent five times out of five. Voting
fixes random errors, not consistent ones, so literal rules have to move to a deterministic grader."
