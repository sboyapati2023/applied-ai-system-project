# AI Interactions Log

This file documents the **agentic workflow** in this project and saves its **intermediate reasoning traces**. The traces show what Claude thought, said, and did at each step, not just the final answer.

---

## Agent workflow

`coding_agent.py` runs a multi-step decision chain. Each stage is a separate step with its own trace:

| Stage | Who decides | What happens | What is traced |
|---|---|---|---|
| **PLAN** | Claude | Reads the README, code, and tests, then returns a structured plan: steps, files, tests to add, risks, and a 0–1 confidence score | Reasoning summary + the plan |
| **Approve** | Human | The developer accepts or rejects the plan before any file changes | Recorded in `report.md` |
| **ACT** | Claude | Chooses its own tool calls (`list_files`, `read_file`, `edit_file`, `write_file`, `run_tests`) and works test-first | Reasoning summaries, messages, every tool call with arguments, every tool result (including refusals) |
| **CHECK** | Plain Python | Re-runs the full suite; fails on any failing, missing, or not-added test, or on code that doesn't compile | Pass/fail with the exact problems |
| **FIX** | Claude | Gets the CHECK report as its next instruction and repairs the cause (up to 3 rounds) | Same as ACT |

### Where traces are saved

Every run writes these files to `.agent_runs/<run_id>/`:

- **`trace.md`:** readable, grouped by stage: PLAN → ACT (round 1) → CHECK (round 1) → FIX (round 2) → …
- **`trace.json`:** the same steps as structured JSON (`stage`, `kind`, `text` / `tool` / `args` / `ok`).
- **`report.md`:** plan, tool-call list, check results, and the final diff.
- **`result.json`:** outcome, rounds, confidence, and per-round failures.

Reasoning summaries come from Claude's adaptive thinking with `display: "summarized"`. The raw chain of thought is never exposed; these are the model's own summaries of its reasoning. `.agent_runs/` is gitignored, so the runs below are copied into the committed folders [logs/](logs/) and [eval/runs/](eval/runs/).

---

## Saved traces

| Run | Task | Files |
|---|---|---|
| Plan only, 2026-11-22 16:22 | Fix: a plan can run past midnight | [logs/2026-11-22_plan_midnight_report.md](logs/2026-11-22_plan_midnight_report.md) |
| Plan only, 2026-11-22 16:43 | Fix: untimed tasks are placed at the end of the day | [trace.md](logs/2026-11-22_plan_untimed-gaps_trace.md) · [trace.json](logs/2026-11-22_plan_untimed-gaps_trace.json) · [result.json](logs/2026-11-22_plan_untimed-gaps_result.json) |
| Evaluation: full plan → act → check runs, 4 bug fixes | See [eval/results.md](eval/results.md) | Table below |

Full runs from the evaluation (each: PLAN → ACT → CHECK, all passed on round 1):

| Case | Reasoning trace | Report (plan, tool log, check, diff) | Structured log |
|---|---|---|---|
| midnight | [midnight-trace.md](eval/runs/midnight-trace.md) | [midnight.md](eval/runs/midnight.md) | [midnight.json](eval/runs/midnight.json) |
| same-name-recur | [same-name-recur-trace.md](eval/runs/same-name-recur-trace.md) | [same-name-recur.md](eval/runs/same-name-recur.md) | [same-name-recur.json](eval/runs/same-name-recur.json) |
| priority-slot | [priority-slot-trace.md](eval/runs/priority-slot-trace.md) | [priority-slot.md](eval/runs/priority-slot.md) | [priority-slot.json](eval/runs/priority-slot.json) |
| untimed-gaps | [untimed-gaps-trace.md](eval/runs/untimed-gaps-trace.md) | [untimed-gaps.md](eval/runs/untimed-gaps.md) | [untimed-gaps.json](eval/runs/untimed-gaps.json) |
| trap | not run: API credits ran out | – | – |

---

## Example trace: planning the "untimed tasks" fix

**Input:**

```bash
python coding_agent.py --plan-only "Fix: tasks with no set time are placed at the end of the day. They should fill the earliest free gap that fits."
```

**Intermediate reasoning (Claude's summarized thinking, verbatim from the trace):**

> I'm planning the change to _place_on_clock: timed tasks get laid out first, sorted by start time, with the cursor pushing later tasks forward on overlap. Untimed tasks would then fill the earliest gap from day_start that fits. Right now timed tasks run sequentially from day_start, and one that starts before day_start gets pushed up to it, so I'm working out how to keep that while adding the gap-filling step.
>
> Next I'm placing the untimed tasks: for each one, I'll look for the earliest start at or after day_start where its duration fits without overlapping any busy interval from the timed pass, then add it to busy. Once everything is placed, the plan gets sorted by start time. I also need to keep the existing conflict test passing, which means iterating timed tasks in the sort_by_time order of the chosen list so the stable sort keeps the shorter Luna breakfast ahead of Mochi's.
>
> I'm checking how the plan's ordering and the README and test-count notes should change, and I'm settling on sorting by integer minutes rather than by the "HH:MM" string so times past midnight order correctly. […]

**Output (an excerpt of the plan):**

```
Risks:
  - The existing test test_generate_daily_plan_records_conflicts depends on the order of plan items
    for same-time timed tasks. The timed path must keep its current iteration and the final sort
    must be stable, keyed on start minutes.
  - Sorting by 'HH:MM' strings can misorder times past midnight (minutes of 1440 or more). Sort by
    integer minutes to avoid this. The past-midnight bug itself is out of scope.
  - New tests must give the owner enough time_available_today, or tasks will be skipped by the
    budget and the tests will fail for the wrong reason.

Confidence: 0.80 (The change is localized to one private method and I traced both existing
placement-sensitive tests against the new logic. Remaining uncertainty comes from app.py and the
UML contents, which I haven't seen, and from small fixture details in the new tests.)
```

**What the trace shows:** the reasoning steps lead directly to the plan's risks. While thinking, Claude checks how an existing test (`test_generate_daily_plan_records_conflicts`) relies on ordering, and that becomes the first listed risk. It notices that string-sorting times breaks past midnight, which becomes the second. The confidence score explains its own uncertainty: two files it hadn't read yet.

---

## Example trace: planning the midnight fix

From [logs/2026-11-22_plan_midnight_report.md](logs/2026-11-22_plan_midnight_report.md). This run was made before reasoning summaries were recorded, so only the plan is saved. It shows the planner finding a problem no existing test covered:

```
Risks:
  - Reasons can contradict each other: `_select_within_budget` already wrote "Scheduled ...
    (N min left)" for an overflowing task. If that line is not replaced, the reasoning says both
    scheduled and skipped. [...]
```

---

## What I verified or fixed manually

- I reviewed each plan before approving it. The agent can't edit anything until I approve.
- I compared plans against the code myself, for example confirming that `_place_on_clock` is where the midnight overflow happens.
- Agent results are not trusted on their own word: `check_work()` re-runs the suite independently, and the evaluation grades each run with hidden tests the agent can't see (see [model_card.md](model_card.md)).
