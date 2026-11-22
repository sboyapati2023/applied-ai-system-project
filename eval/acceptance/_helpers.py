"""Shared helpers for the hidden acceptance tests."""


def minutes(hhmm: str) -> int:
    hours, mins = hhmm.split(":")
    return int(hours) * 60 + int(mins)


def assert_no_overlaps(plan) -> None:
    spans = sorted((minutes(item.start), minutes(item.end), item.task.description) for item in plan)
    for (_, end, first), (start, _, second) in zip(spans, spans[1:]):
        assert start >= end, f"'{first}' overlaps '{second}'"
