# Experiment: Llama 3.3 70B vs Ministral 8B in the same harness

Experiment `33037e1d` ("m vs l (b-s-ft)"), run 2026-11-23. Every number below comes from the experiment page
(`api/src/stats.ts`); the method is explained in [STATISTICS.md](STATISTICS.md).

## One axis: the model
Same harness (the baseline tool loop), same tools (`file` + `terminal`), same skills (`acme-billing`,
`acme-style`), memory off, temperature 0, max 10 steps, max 1,024 output tokens per reply, timeout 300 s.
**Only difference: the model.**

| Config | Model | Price per 1M tokens (in / out) |
|--------|-------|--------------------------------|
| A = `b-l-s-ft@v1` | `meta-llama/llama-3.3-70b-instruct` | $0.10 / $0.32 |
| B = `b-m-s-ft@v1` | `mistralai/ministral-8b-2512` | $0.15 / $0.15 |

Both reached through OpenRouter, no provider pinning.

## Hypothesis
Not pre-registered: this write-up was done after the run, so the hypothesis below is the prior we would have
stated, not a prediction we committed to beforehand.

1. The 70B model passes more tasks than the 8B model, mainly on the multi-step `hard-*` and `code-*` tasks.
2. Both do equally well on easy tasks (`arith`, `date`, `file`) and equally badly on the memory tasks (memory is off).
3. The 70B model costs more per trial.

## Setup
- 26 tasks × N=5 trials × 2 configs = **260 trials**, all finished: 0 errors, 0 cancelled. Wall time about 5 minutes.
- All 26 tasks, including `mem-01`/`mem-02` (both configs fail them: memory is off) and the 3 judge-graded tasks.
- **Why N=5:** the power simulation (DESIGN.md §6) gives ~73% power for a large, skills-sized effect at N=5. This run
  can detect a large difference between the models, not a small one.
- Judge: `qwen3.5-9b`, 1 vote (the `models.yaml` setting at run time).

## Judge caveat
The judge failed validation (see [JUDGE_VALIDATION.md](JUDGE_VALIDATION.md#validation-results-after-the-changes)). In
this run, **20 of the 30 judge-graded trials** got "judge returned unparseable output" (qwen's thinking text instead
of JSON), which counts as a fail. So `judge-01`, `judge-02` and `skill-05` mostly measure the judge, not the models.
Results are therefore given with and without those 3 tasks. The verdict is the same either way.

## Results

| Config | Pass rate | 95% CI | Avg cost / trial | Total cost | Avg latency | p90 latency | Avg tokens | LLM calls / trial | Errors |
|--------|-----------|--------|------------------|------------|-------------|-------------|------------|-------------------|--------|
| A: Llama 3.3 70B | 28.5% | 13.1% – 45.4% | $0.00020 | $0.0261 | 3.3 s | 6.9 s | 1,672 | 2.24 | 0 |
| B: Ministral 8B | 35.4% | 20.0% – 53.1% | $0.00032 | $0.0419 | 3.5 s | 6.6 s | 2,150 | 2.55 | 0 |

**A − B = −6.9 points, 95% CI [−23.1, +9.2], bootstrap p ≈ 0.41 → no significant difference.**

Without the 3 judge-graded tasks (23 tasks): A 27.8% [11.3, 47.0], B 37.4% [20.0, 55.7];
A − B = −9.6 points, 95% CI [−27.8, +8.7], p ≈ 0.31 → **no significant difference**.

**Per-task deltas** (tasks where the rates differ; the other 16 tasks are identical):

| Task | A (Llama) | B (Ministral) | Delta | Label |
|------|-----------|---------------|-------|-------|
| code-01 | 0/5 | 5/5 | −1.0 | B better |
| code-04 | 0/5 | 5/5 | −1.0 | B better |
| skill-01 | 5/5 | 0/5 | +1.0 | A better |
| arith-01 | 5/5 | 1/5 | +0.8 | A better |
| code-03 | 2/5 | 5/5 | −0.6 | B better |
| hard-01 | 0/5 | 3/5 | −0.6 | B better |
| hard-03 | 0/5 | 3/5 | −0.6 | B better |
| hard-04 | 0/5 | 1/5 | −0.2 | noise |
| judge-02 | 1/5 | 0/5 | +0.2 | noise (judge) |
| skill-05 | 3/5 | 2/5 | +0.2 | noise (judge) |

The per-task CIs of 0/5-vs-5/5 rows collapse to a single point and overstate certainty (see STATISTICS.md); read
this table as "where the difference comes from", not as per-task proof.

**By task category** (same paired bootstrap within each category; categories under 5 tasks get no winner):

| Category | Tasks | A (Llama) | B (Ministral) | Δ | 95% CI | Reading | Cost / trial A · B |
|----------|-------|-----------|---------------|---|--------|---------|--------------------|
| coding | 4 | 10% | 75% | −65 | [−100, −25] | too few tasks | $0.00013 · $0.00020 |
| hard | 5 | 0% | 28% | −28 | [−52, −4] | B better | $0.00024 · $0.00094 |
| reasoning | 5 | 60% | 44% | +16 | [0, +48] | noise | $0.00011 · $0.00012 |
| skill | 5 | 32% | 8% | +24 | [0, +64] | noise | $0.00039 · $0.00020 |
| files / memory / writing | 3 / 2 / 2 | 67% / 0% / 20% | 67% / 0% / 10% | 0 / 0 / +10 | – | same / same / too few | – |

The overall "no difference" hides a split: Ministral leads on coding and hard tasks, where Llama tends not to
use its tools (Ministral costs about 4× more per hard trial, most likely because it actually runs the multi-step tool work); Llama
leans ahead on reasoning and skill tasks. With 7 categories tested, one false "winner" is plausible by chance,
so the hard-task result is a lead that the trajectories below support, not a finding on its own.

**Failure categories:** A: `wrong_answer` 88, `step_exhaustion` 5. B: `wrong_answer` 81, `step_exhaustion` 2,
`hallucinated_tool` 1. Both fail mostly with `wrong_answer`, which hides the real cause for A (next section).

## Inspected trajectories
Links work while the platform runs locally: `http://localhost:3000/#/trials/<id>`.

1. **`code-01`, Llama fails by describing instead of acting** (`33037e1d-4` vs Ministral `33037e1d-5`).
   Llama's only reply is the text `write_file(path="solution.py", content="def is_palindrome...")`: a correct
   function written as a tool call *in plain text*, not as a real tool call. No file is created, and the hidden
   unit test fails with `No module named 'solution'`. Ministral makes a real `write_file` call and passes. All 5
   Llama trials of `code-01` do this. On `code-04` and `hard-01` Llama does the same thing in another form: it
   pastes the code into its answer instead of writing the file (`hard-01`: "revenue.json was not created").
   Earlier trial `9022b24e-22` (`hard-02`) shows the same habit.
2. **`skill-01`, Ministral reads the rule but applies it wrong** (`33037e1d-41` vs Llama `33037e1d-40`).
   Both call `read_skill(acme-billing)`. The rule is a 3-letter customer code; Ministral answers
   `ACME-25-GLOB-0007` (4 letters) in all 5 trials. Llama recovers from a broken first shell command, reads the
   skill, builds the ID with `run_shell`, and answers `ACME-25-GLO-0007`.
3. **`arith-01`, Ministral does mental arithmetic** (`33037e1d-1`). 09:40 → 13:15 is 215 minutes; Ministral
   answers 255 without using a tool (4 of 5 trials wrong). Llama gets 5/5.

## Conclusion
The hypothesis is **not supported**: the 70B model did not beat the 8B model. Ministral scored 6.9 points higher,
but the 95% CI [−23.1, +9.2] includes 0, so at this power (26 tasks, N=5) the difference is noise.

The two models fail in different ways, which the aggregate hides:
- **Llama 3.3 70B often does not act.** On file-producing tasks it writes code, or even the tool call itself, as
  chat text instead of calling the tool. That costs it `code-01`, `code-04`, `hard-01` and `hard-03`. This is a
  tool-calling reliability problem (possibly provider-dependent, since routing was not pinned), not a reasoning problem.
- **Ministral 8B acts, but is less precise:** a wrong invoice-ID format after reading the rule, and mental-arithmetic errors.

Cost: Ministral costs **60% more per trial** ($0.00032 vs $0.00020). It uses 29% more tokens and has a higher input
price. Latency is the same.

Next steps: pin providers (`provider_routing`) so tool-calling behaviour is not provider luck; add a failure rule
for "tool call written as text" (today it shows up as `wrong_answer`); run N=10 for a decision.

## Actual spend
Models $0.0680 + judge $0.0025 = **$0.0705** for 260 trials (about $0.00027 per trial).

## Not run: Hermes skills on vs off
The originally planned experiment (Hermes + gpt-4o-mini, skills on vs off, 24 tasks, N=10, about $0.45) was not
run for this submission. A first attempt (`e181b969`) was discarded after OpenRouter credits ran out mid-run; it
exposed the provider-error and judge bugs described in JUDGE_VALIDATION.md.
