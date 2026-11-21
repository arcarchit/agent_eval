# Experiment: does enabling skills help Hermes?

> **First attempt (e181b969) was discarded:** OpenRouter credits ran out mid-run, and 116 of 210 trials got
> HTTP 402. The platform graded them as `wrong_answer`, which was a bug (now fixed: such trials end as `error`
> and are excluded). The run also showed exact match was too strict and the judge was unstable; see
> `docs/internal/JUDGE_VALIDATION.md`. Good trajectories from it: `e181b969-39` vs `-38` (skill-05), `-115` vs `-30` (skill-01).
>
> **Status: setup ready, results not yet filled in.** Running this needs an OpenRouter key and costs
> about $0.40. Fill in every `TODO` from the experiment page. Do not edit numbers by hand.

## One axis
Same harness (Hermes), same model (`gpt-4o-mini`), same tools (`file`), memory off, temperature 0, max 10 steps.
**Only difference: skills.**

| Config | Skills |
|--------|--------|
| `hermes-skills` | `acme-billing`, `acme-style` |
| `hermes-noskills` | none |

## Hypothesis
1. With skills, Hermes passes the 5 `skill-*` tasks much more often: they depend on made-up ACME rules
   (invoice ID format, late-fee rule, fiscal calendar, JSON schema, email style) that a model cannot know.
2. On the other 14 tasks there is no significant difference.
3. Skills cost more tokens per trial (the skills index is added to the prompt, and `skill_view` adds a step).

Prediction: overall difference A − B of about +15 to +20 points, with the 95% CI above 0, all of it coming from `skill-*`.

## Setup
- 19 tasks × N=5 trials × 2 configs = 190 trials. `mem-01`/`mem-02` are excluded: memory is off in both configs, so
  they can only fail, and with skills on, Hermes was seen saving the fact as a skill instead, which would blur the one axis. Why N=5: see `docs/DESIGN.md` §6.
- Hermes commit `611f0ab7`, task snapshot stored in the experiment row (id: `TODO`).

Reproduce with the UI, or with curl:
```bash
B=http://localhost:3000/api; H='Content-Type: application/json'
curl -XPOST $B/configs -H "$H" -d '{"name":"hermes-skills","harness":"hermes","model":"gpt-4o-mini","skills":["acme-billing","acme-style"],"tools":["file"]}'
curl -XPOST $B/configs -H "$H" -d '{"name":"hermes-noskills","harness":"hermes","model":"gpt-4o-mini","tools":["file"]}'
# all tasks except mem-01 / mem-02 (see Setup above)
curl -XPOST $B/experiments -H "$H" -d '{"name":"skills on vs off","config_ids":["hermes-skills@v1","hermes-noskills@v1"],"trials_per_task":5,
  "task_ids":["arith-01","arith-02","code-01","code-02","code-03","code-04","date-01","file-01","file-02","file-03","judge-01","judge-02","logic-01","skill-01","skill-02","skill-03","skill-04","skill-05","text-01"]}'
```

## Judge validation (run first)
`python worker/judge_validation.py` → accuracy `TODO`, Cohen's kappa `TODO`, false passes `TODO`, false fails `TODO`.

## Results
| Config | Pass rate | 95% CI | Avg cost / trial | Avg latency | Avg tokens | Errors |
|--------|-----------|--------|------------------|-------------|------------|--------|
| hermes-skills | TODO | TODO | TODO | TODO | TODO | TODO |
| hermes-noskills | TODO | TODO | TODO | TODO | TODO | TODO |

A − B = `TODO`, 95% CI `TODO`, bootstrap p ≈ `TODO` → verdict: `TODO`

Per-task deltas (top rows of the compare table): `TODO`

Failure categories: `TODO` (expect `wrong_answer` on `skill-*` for no-skills)

## Inspected trajectories
1. `skill-01`, no skills (trial link `TODO`): expected to guess an invoice ID format like `INV-2025-...`. → `TODO`
2. `skill-01`, with skills: expected to call `skill_view(acme-billing)` and then answer `ACME-25-GLO-0007`. → `TODO`
3. One surprise (a non-skill task that changed, or a skills trial that still failed) → `TODO`

## Conclusion
`TODO`, for example: "Skills improve pass rate by X points (CI [a, b]); the gain is entirely on skill-dependent tasks;
cost per trial rises by Y%. On general tasks the difference is within noise."

## Actual spend
`TODO` (sum of total cost for both configs, plus judge cost from the CSV export)
