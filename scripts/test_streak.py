#!/usr/bin/env python3
"""Regression tests for the current-streak anchor in generate_cards.summarise.

The bug these exist to prevent: on 2026-09-12 the card rendered "Current
Streak 0" on top of a live 66-day run. The render fires at 01:1x Berlin, which
is 23:1x UTC, and the contribution calendar is bucketed in UTC - so the job was
judging a UTC day that still had 45 minutes left to run. Anchoring on the
Berlin date burned the grace day on a UTC day that had not started and then
read the open UTC day as a finished zero.

Run: python3 scripts/test_streak.py
"""

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import generate_cards as gc  # noqa: E402


def with_clock(berlin_today: date, utc_now: datetime):
    """Pin both clocks summarise() reads, then restore them."""
    class FakeDate(gc.date):
        @classmethod
        def today(cls):
            return berlin_today

    class FakeDatetime(gc.datetime):
        @classmethod
        def now(cls, tz=None):
            return utc_now

    return FakeDate, FakeDatetime


def summarise_at(days, berlin_today, utc_now):
    real_date, real_datetime = gc.date, gc.datetime
    gc.date, gc.datetime = with_clock(berlin_today, utc_now)
    try:
        return gc.summarise(days)
    finally:
        gc.date, gc.datetime = real_date, real_datetime


def calendar(through: date, count: int = 4, days_back: int = 90) -> dict:
    """A dict of contribution days, every one active, ending at `through`."""
    return {through - timedelta(days=i): count for i in range(days_back)}


FAILURES: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}: got {got!r}, want {want!r}")
        FAILURES.append(name)


# The real 2026-09-12 shape: an unbroken run through Sep 10, then the open UTC
# day (Sep 11) still empty at render time, and Berlin already on Sep 12.
SEP10, SEP11, SEP12 = date(2026, 9, 10), date(2026, 9, 11), date(2026, 9, 12)
NIGHT = datetime(2026, 9, 11, 23, 14, tzinfo=timezone.utc)   # 01:14 Berlin Sep 12
DAY = datetime(2026, 9, 12, 10, 0, tzinfo=timezone.utc)      # 12:00 Berlin Sep 12

print("night render, open UTC day still empty (the 2026-09-12 regression)")
s = summarise_at(calendar(SEP10, days_back=65), SEP12, NIGHT)
check("streak survives the open UTC day", s["current"], 65)
check("streak ends on the last complete day", s["current_end"], SEP10)

print("night render, the day's contribution has landed")
days = calendar(SEP10, days_back=65) | {SEP11: 1}
s = summarise_at(days, SEP12, NIGHT)
check("late contribution extends the streak", s["current"], 66)
check("anchored on the open UTC day", s["current_end"], SEP11)

print("daytime render, today already active")
days = calendar(SEP10, days_back=65) | {SEP11: 1, SEP12: 3}
s = summarise_at(days, SEP12, DAY)
check("today counts", s["current"], 67)
check("anchored on today", s["current_end"], SEP12)
check("longest keeps up with current", s["longest"], 67)

print("daytime render, today still quiet")
days = calendar(SEP10, days_back=65) | {SEP11: 1, SEP12: 0}
s = summarise_at(days, SEP12, DAY)
check("a quiet today does not break the streak", s["current"], 66)
check("anchored on yesterday", s["current_end"], SEP11)

print("a genuine break still reads as broken")
days = calendar(SEP10, days_back=65) | {SEP11: 0, SEP12: 0}
s = summarise_at(days, SEP12, DAY)
check("two complete quiet days end the streak", s["current"], 0)
check("no end date on a dead streak", s["current_end"], None)

# The hard one: a real break happening *inside* the window the bug lived in.
# Berlin is a day ahead of UTC here, so the anchor gets a free skip over a UTC
# date that does not exist yet - and it must still not hand out a second grace
# day and resurrect a streak that genuinely ended.
print("a genuine break during the night window is still broken")
days = calendar(date(2026, 9, 9), days_back=65) | {SEP10: 0, SEP11: 0}
s = summarise_at(days, SEP12, NIGHT)
check("phantom day is not a second grace day", s["current"], 0)
check("no end date on a dead streak at night", s["current_end"], None)

# Same arithmetic in winter, when Berlin is UTC+1 rather than UTC+2 and the
# mismatch window is an hour rather than two. The code compares dates and never
# touches the offset, so this should behave identically.
print("CET: the mismatch window is shorter but behaves the same")
JAN14, JAN15 = date(2027, 1, 14), date(2027, 1, 15)
CET_NIGHT = datetime(2027, 1, 14, 23, 20, tzinfo=timezone.utc)  # 00:20 Berlin Jan 15
days = calendar(date(2027, 1, 13), days_back=40)
check("open UTC day empty, streak intact",
      summarise_at(days, JAN15, CET_NIGHT)["current"], 40)
check("open UTC day active, streak extends",
      summarise_at(days | {JAN14: 2}, JAN15, CET_NIGHT)["current"], 41)

print()
if FAILURES:
    sys.exit(f"{len(FAILURES)} failing: {', '.join(FAILURES)}")
print("all streak anchor tests passed")
