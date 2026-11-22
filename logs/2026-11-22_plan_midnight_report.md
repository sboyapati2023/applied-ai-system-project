# Agent run 20261122-162220

**Task:** Fix: a plan can run past midnight

## Baseline

36 passed, 0 failed, 0 skipped

## Plan

```
Summary: Stop the daily plan from running past midnight. Today `Scheduler._place_on_clock` moves a cursor forward from `owner.day_start` and never checks the 24-hour limit, so a late preferred time, a late `day_start`, or tasks pushed back by earlier ones can produce end times like "24:30". The fix keeps only tasks that end at or before midnight (1440 minutes). Any task that would overflow moves to `self.skipped` with a clear reason, and the cursor stays put so later, shorter tasks can still fit. The change lives only in `pawpal_system.py`, so the logic stays independent of Streamlit and no public API changes.

Steps:
  1. Read `app.py`, `diagrams/uml.mmd`, and how `generate_daily_plan`, `skipped` and `reasons` are used, to confirm no caller expects end times past 24:00.
  2. Write the new failing tests in `tests/test_pawpal.py` first (listed below) and run pytest to confirm they fail for the right reason, such as a plan entry ending "24:30" or "25:00".
  3. Add a module-level constant `MINUTES_PER_DAY = 1440` next to the other constants in `pawpal_system.py`.
  4. In `Scheduler._place_on_clock`, compute `start = max(cursor, task.start_minutes)` for timed tasks (or `cursor` for untimed ones) and `end = start + task.duration`.
  5. If `end > MINUTES_PER_DAY`, do not add the task to the plan. Append it to `self.skipped`, do not advance the cursor, and record a reason such as "Skipped '<desc>' for <pet>: would start at HH:MM and run past midnight."
  6. Otherwise place the task as today and set `cursor = end`. A task ending exactly at midnight is allowed and keeps the existing `_to_hhmm` output of "24:00", so no new formatting rules are introduced.
  7. Prevent contradictory reasoning: an overflowing task should not keep its earlier "Scheduled ..." line from `_select_within_budget`. Track each chosen task's reason index in a private dict built in `_select_within_budget` and replace that entry with the skip reason in `_place_on_clock`. Keep this private, with no new public attribute.
  8. Update the `_place_on_clock` docstring (and the `generate_daily_plan` docstring if needed) to say that tasks which would end after midnight are skipped.
  9. Run the full suite and confirm all 20 existing PawPal+ tests still pass unchanged, especially `test_generate_daily_plan_records_conflicts` and `test_next_occurrence_is_not_in_todays_plan`.
  10. Update `README.md`.
  11. In the README's known-bugs list, mark "A plan can run past midnight" as fixed (or remove it).
  12. Update the test counts in the README Setup comment ("36 tests: 20 for PawPal+"), in the Testing summary headline, and in the PawPal+ scheduler row so they match the new totals.
  13. Check `diagrams/uml.mmd`. No public class, method or attribute changes, so it should need no edit. If a public constant or attribute ends up being added after all, update the diagram to match.
  14. Run `python main.py` as a smoke check. Its tasks all end well before midnight, so its output should be unchanged.

Files to change:
  - pawpal_system.py
  - tests/test_pawpal.py
  - README.md

Tests to add:
  - `test_task_that_would_run_past_midnight_is_skipped`: a 60-min task with preferred time 23:30 is left out of the plan, appears in `scheduler.skipped`, and `get_reasoning()` mentions midnight and does not also say it was scheduled.
  - `test_pushed_back_task_that_would_cross_midnight_is_skipped`: two 20-min tasks at 23:30 give the first 23:30-23:50, and the second (which would run 23:50-00:10) is skipped instead of wrapping.
  - `test_late_day_start_cannot_push_untimed_task_past_midnight`: an owner with `day_start` "23:50" and an untimed 30-min task gets an empty plan, with the task in `skipped`.
  - `test_task_ending_exactly_at_midnight_is_kept`: a 30-min task at 23:30 is scheduled as ("23:30", "24:00") to pin the boundary.
  - `test_shorter_later_task_still_fits_after_midnight_overflow`: after a long late task is skipped, a later 5-min task at 23:50 is still placed at 23:50-23:55, which shows the cursor did not advance.
  - `test_no_plan_entry_ends_after_midnight`: a mixed set of late timed and untimed tasks produces only plan entries whose end minutes are 1440 or less.

Risks:
  - Reasons can contradict each other: `_select_within_budget` already wrote "Scheduled ... (N min left)" for an overflowing task. If that line is not replaced, the reasoning says both scheduled and skipped. Even when it is replaced, later tasks' "min left" numbers will understate the remaining budget, because the skipped task's minutes are not given back. This is acceptable for a small fix, but it should be noted in the code comment.
  - Freed budget is not reused: a task skipped for budget reasons will not be reconsidered after a midnight skip frees minutes. This keeps the change small, but it is a behavior limit.
  - Boundary choice: allowing an end of exactly "24:00" is a judgment call. If the UI or a reviewer prefers "00:00" or a strict `end < 1440`, the boundary test would need to change in a follow-up, not silently.
  - Overlap with the other known bugs: the priority-push and untimed-gap bugs also change `_place_on_clock`. Later fixes must keep the midnight guard, and these new tests protect it.
  - `app.py` may display `scheduler.skipped` or the reasoning with assumptions about budget-only skips. Check the wording still reads correctly. No Streamlit change is expected.
  - Existing tests: none should change. If any existing test turns out to rely on post-midnight times, stop and report it instead of editing it.
```

Stopped after planning (--plan-only).
## Diff

```diff
(no changes)
```
