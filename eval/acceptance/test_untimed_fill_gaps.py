"""Known gap: tasks with no set time are placed at the end of the day."""
from datetime import date

from _helpers import assert_no_overlaps, minutes
from pawpal_system import Owner, Pet, Scheduler, Task

DAY = date(2026, 11, 22)


def test_untimed_task_fills_earlier_free_gap():
    owner = Owner("Jordan", 120, day_start="08:00")
    pet = Pet("Mochi", "dog")
    owner.add_pet(pet)
    pet.add_task(Task("Meds", 5, "high", time="12:00", due_date=DAY))
    pet.add_task(Task("Fetch", 15, "medium", due_date=DAY))  # no preferred time

    plan = Scheduler(owner).generate_daily_plan(day=DAY)

    by_name = {item.task.description: item for item in plan}
    assert by_name["Meds"].start == "12:00"
    assert minutes(by_name["Fetch"].end) <= minutes("12:00"), (by_name["Fetch"].start, by_name["Fetch"].end)
    assert_no_overlaps(plan)
