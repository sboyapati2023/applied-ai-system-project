"""Known gap: two daily tasks with the same name don't both recur."""
from datetime import date, timedelta

from pawpal_system import Owner, Pet, Scheduler, Task

DAY = date(2026, 11, 22)


def test_two_daily_tasks_with_same_name_both_recur():
    owner = Owner("Jordan", 120)
    pet = Pet("Mochi", "dog")
    owner.add_pet(pet)
    morning = Task("Walk", 30, "high", time="07:30", frequency="daily", due_date=DAY)
    evening = Task("Walk", 30, "high", time="18:00", frequency="daily", due_date=DAY)
    pet.add_task(morning)
    pet.add_task(evening)
    scheduler = Scheduler(owner)

    scheduler.mark_task_complete(morning)
    scheduler.mark_task_complete(evening)

    tomorrow = [t for t in pet.tasks if t.due_date == DAY + timedelta(days=1)]
    assert sorted(t.time for t in tomorrow) == ["07:30", "18:00"]


def test_recompleting_still_does_not_duplicate():
    owner = Owner("Jordan", 120)
    pet = Pet("Mochi", "dog")
    owner.add_pet(pet)
    walk = Task("Walk", 30, "high", time="07:30", frequency="daily", due_date=DAY)
    pet.add_task(walk)
    scheduler = Scheduler(owner)

    scheduler.mark_task_complete(walk)
    walk.mark_incomplete()
    scheduler.mark_task_complete(walk)

    assert len(pet.tasks) == 2
