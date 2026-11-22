"""Known gap: a low-priority task can push a high-priority one later."""
from datetime import date

from _helpers import assert_no_overlaps
from pawpal_system import Owner, Pet, Scheduler, Task

DAY = date(2026, 11, 22)


def test_high_priority_task_keeps_its_preferred_time():
    owner = Owner("Jordan", 120, day_start="07:00")
    mochi, luna = Pet("Mochi", "dog"), Pet("Luna", "cat")
    owner.add_pet(mochi)
    owner.add_pet(luna)
    mochi.add_task(Task("Nail trim", 30, "low", time="07:45", due_date=DAY))
    luna.add_task(Task("Meds", 5, "high", time="08:00", due_date=DAY))

    plan = Scheduler(owner).generate_daily_plan(day=DAY)

    starts = {item.task.description: item.start for item in plan}
    assert starts.get("Meds") == "08:00", starts
    assert "Nail trim" in starts, "the low-priority task should still be scheduled"
    assert_no_overlaps(plan)
