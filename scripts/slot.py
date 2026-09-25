#!/usr/bin/env python3
"""Which twice-daily refresh slot is this, and has it already been done?

The cards refresh at 1 AM and 1 PM IST. GitHub cron is best-effort - it starts
runs late and sometimes drops them outright (the lone 07:30 UTC run on
2026-09-25 never started) - so streak.yml fires several retry crons after each
target. This gate makes the first one that lands do the work and every retry
behind it no-op, which gives exactly one publish per slot, two a day.

A slot is named by the IST half-day that begins at its target:
  01:00-12:59 IST -> "<date> AM",  13:00-00:59 IST -> "<date> PM"
IST has no DST, so the boundaries are fixed in UTC too (19:30 and 07:30).

The done-marker is the `<!-- slot ... -->` stamp generate_cards.py writes into
every card, read back from streak.svg on the `analytics` branch - the last card
actually published. The workflow extracts it to CARD_FILE; a missing file (the
branch does not exist yet) means the slot is not done.

CLI (used by the workflow): reads EVENT, CARD_FILE, optional NOW_UTC, and
appends `run=` and `slot=` lines to $GITHUB_OUTPUT.
"""

import os
import re
import sys
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
FIRST_TARGET_HOUR = 1   # 1 AM IST; the second target is 12h later


def slot_for(now: datetime) -> str:
    shifted = now.astimezone(IST) - timedelta(hours=FIRST_TARGET_HOUR)
    return f"{shifted.date().isoformat()} {'AM' if shifted.hour < 12 else 'PM'}"


def slot_in(card_text: str) -> str | None:
    m = re.search(r"<!-- slot (\d{4}-\d{2}-\d{2} [AP]M) -->", card_text)
    return m.group(1) if m else None


def should_run(event: str, now: datetime, card_text: str) -> bool:
    if event != "schedule":
        return True
    return slot_in(card_text) != slot_for(now)


def main() -> None:
    event = os.environ.get("EVENT", "schedule")
    card_file = os.environ.get("CARD_FILE", "")
    raw_now = os.environ.get("NOW_UTC")
    now = (datetime.fromisoformat(raw_now.replace("Z", "+00:00")) if raw_now
           else datetime.now(timezone.utc))

    try:
        with open(card_file) as fh:
            card_text = fh.read()
    except (FileNotFoundError, IsADirectoryError):
        card_text = ""

    current, done = slot_for(now), slot_in(card_text)
    run = should_run(event, now, card_text)
    print(f"event={event} slot={current} last_done={done} -> "
          f"{'run' if run else 'hold, this slot is already published'}")

    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        sys.exit("FATAL: GITHUB_OUTPUT is not set")
    with open(out, "a") as fh:
        fh.write(f"run={'true' if run else 'false'}\nslot={current}\n")


if __name__ == "__main__":
    main()
