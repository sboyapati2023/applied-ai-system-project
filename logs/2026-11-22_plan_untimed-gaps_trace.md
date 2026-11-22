# Reasoning trace: run 20261122-164323

**Task:** Fix: tasks with no set time are placed at the end of the day. They should fill the earliest free gap that fits.

## PLAN

> **Reasoning (summary):** I'm planning the change to _place_on_clock: timed tasks get laid out first, sorted by start time, with the cursor pushing later tasks forward on overlap. Untimed tasks would then fill the earliest gap from day_start that fits. Right now timed tasks run sequentially from day_start, and one that starts before day_start gets pushed up to it, so I'm working out how to keep that while adding the gap-filling step.
> 
> Next I'm placing the untimed tasks: for each one, I'll look for the earliest start at or after day_start where its duration fits without overlapping any busy interval from the timed pass, then add it to busy. Once everything is placed, the plan gets sorted by start time. I also need to keep the existing conflict test passing, which means iterating timed tasks in the sort_by_time order of the chosen list so the stable sort keeps the shorter Luna breakfast ahead of Mochi's.
> 
> I'm checking how the plan's ordering and the README and test-count notes should change, and I'm settling on sorting by integer minutes rather than by the "HH:MM" string so times past midnight order correctly. I'm also leaning toward leaving the UML alone, since the change is to a private method only.
> 
> I'm working out the gap-filling step for untimed tasks: walk the busy intervals in order and take the first gap from day_start that fits the duration. Since the timed pass leaves non-overlapping intervals, that walk only needs to advance the candidate start past each interval it collides with. I also want two tests, one where an untimed task slots into a gap before a later timed task and one where a gap is too small to hold it.
> 
> I'm sketching out the test cases now: one where an untimed task fills a gap before a later timed task, one where a gap is too small and the task lands after it, and a boundary case where a task fits exactly into a gap. I also want a test for several untimed tasks filling gaps in priority order, a check that the plan comes back chronologically sorted, and one confirming timed tasks keep their preferred times. I'll build a custom owner with enough budget for these.

**Plan returned:**

```
Summary: Change Scheduler._place_on_clock so timed tasks are placed first, keeping their preferred times and the current push-later-on-overlap behavior. Untimed tasks then go, in the priority order they were chosen, into the earliest free gap at or after owner.day_start that fits their duration. The returned plan is sorted chronologically. This is an internal change only; no public signatures change.

Steps:
  1. Read pawpal_system.py, app.py, tests/test_pawpal.py, README.md and diagrams/uml.mmd to confirm the only placement logic is in Scheduler._place_on_clock and that app.py and main.py only consume the plan and do not depend on its internal order.
  2. Write the new tests in tests/test_pawpal.py first. Run pytest and confirm they fail against the current code.
  3. Rewrite Scheduler._place_on_clock in pawpal_system.py: (a) split `tasks` into timed and untimed lists, preserving input order; (b) place timed tasks exactly as now: iterate sort_by_time(timed) with cursor starting at day_start and start = max(cursor, task.start_minutes), recording (start_min, end_min, task); (c) for each untimed task in input order (already priority order from _select_within_budget), call a new private helper _earliest_gap(busy, earliest, duration) and record the slot; (d) return the ScheduledTasks sorted by integer start minutes (stable sort).
  4. Add the private helper Scheduler._earliest_gap(busy: list[tuple[int, int]], earliest: int, duration: int) -> int, with a docstring. It walks the busy intervals sorted by start, keeping candidate = earliest. If candidate + duration <= interval_start it returns candidate; otherwise it sets candidate = max(candidate, interval_end). If no gap is found it returns candidate after the last interval. Busy intervals never overlap, because timed placement is sequential and each untimed slot is added after it is placed.
  5. Update the _place_on_clock docstring to say that untimed tasks fill the earliest free gap from day start.
  6. Re-run the full suite. Check test_generate_daily_plan_records_conflicts: two timed 08:00 tasks must still give 08:00-08:05 then 08:05-08:15, because the timed path is unchanged and the stable sort keeps that order. Check test_next_occurrence_is_not_in_todays_plan: an untimed walk is still placed at 08:00.
  7. Run `python main.py` and check that the demo schedule is sensible. 'Fetch in yard' should now land in a gap instead of after the evening tasks, if it is chosen within the budget.
  8. In README.md, mark 'Tasks with no set time are placed at the end of the day' as fixed in the known-bugs list. Update the test counts: 36 tests becomes 36+N, and PawPal+ goes from 20 to 20+N, in both the Setup and Testing summary sections.
  9. Leave diagrams/uml.mmd unchanged, because only private methods change. If the UML lists private methods, add _earliest_gap so it stays accurate.

Files to change:
  - pawpal_system.py
  - tests/test_pawpal.py
  - README.md

Tests to add:
  - test_untimed_task_fills_gap_before_later_timed_task: day_start 08:00, a timed walk at 10:00 for 30 min, an untimed 15-min play; play is scheduled 08:00-08:15, not after the walk.
  - test_untimed_task_skips_gap_too_small: timed tasks at 08:00-08:30 and 08:40-09:00, an untimed 15-min task; it is placed 09:00-09:15 because the 10-min gap is too short.
  - test_untimed_task_fits_exact_size_gap: same timed tasks, an untimed 10-min task; it is placed exactly 08:30-08:40 (boundary case, back-to-back allowed).
  - test_multiple_untimed_tasks_fill_gaps_by_priority_without_overlap: a high-priority untimed task gets the earliest gap before a lower-priority one, and no two plan items overlap.
  - test_timed_tasks_keep_preferred_times_when_untimed_present: adding untimed tasks does not move any timed task's start time.
  - test_daily_plan_is_in_chronological_order: an untimed task placed in an early gap appears in the plan before later timed tasks (plan sorted by start).

Risks:
  - The existing test test_generate_daily_plan_records_conflicts depends on the order of plan items for same-time timed tasks. The timed path must keep its current iteration and the final sort must be stable, keyed on start minutes.
  - The plan used to be ordered with untimed tasks last. If app.py or main.py relies on that order, for example by indexing, their output changes. The plan returns chronological order, which should only improve display.
  - Sorting by 'HH:MM' strings can misorder times past midnight (minutes of 1440 or more). Sort by integer minutes to avoid this. The past-midnight bug itself is out of scope.
  - New tests must give the owner enough time_available_today, or tasks will be skipped by the budget and the tests will fail for the wrong reason.
  - The README test counts must match the actual number of tests added, or the documentation will be inaccurate.

Confidence: 0.80 (The change is localized to one private method and I traced both existing placement-sensitive tests against the new logic. Remaining uncertainty comes from app.py and the UML contents, which I haven't seen, and from small fixture details in the new tests.)
```

