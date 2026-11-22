"""Known gap: a plan can run past midnight."""
from datetime import date

from _helpers import assert_no_overlaps, minutes
from pawpal_system import Owner, Pet, Scheduler, Task

DAY = date(2026, 11, 22)


def test_plan_never_runs_past_midnight():
    owner = Owner("Jordan", 240, day_start="23:00")
    pet = Pet("Mochi", "dog")
    owner.add_pet(pet)
    for name in ["Walk", "Brush", "Play"]:
        pet.add_task(Task(name, 30, "high", due_date=DAY))
    scheduler = Scheduler(owner)

    plan = scheduler.generate_daily_plan(day=DAY)

    assert all(minutes(item.end) <= 24 * 60 for item in plan), [(i.start, i.end) for i in plan]
    assert_no_overlaps(plan)
    # Nothing is silently lost: every task is either scheduled or reported as skipped.
    assert {i.task.description for i in plan} | {t.description for t in scheduler.skipped} == {"Walk", "Brush", "Play"}


def test_late_preferred_time_does_not_cross_midnight():
    owner = Owner("Jordan", 120)
    pet = Pet("Mochi", "dog")
    owner.add_pet(pet)
    pet.add_task(Task("Late walk", 60, "high", time="23:30", due_date=DAY))
    scheduler = Scheduler(owner)

    plan = scheduler.generate_daily_plan(day=DAY)

    assert all(minutes(item.end) <= 24 * 60 for item in plan)
    assert plan or scheduler.skipped
