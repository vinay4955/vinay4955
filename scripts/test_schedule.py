#!/usr/bin/env python3
"""Tests for the twice-a-day card refresh: slot gate, render stamps, verify.

Two failures these pin, both from 2026-09-24/25:

  1. The workflow was rewritten to verify a `<!-- rendered DAY -->` stamp that
     generate_cards.py never wrote, so every run went red with "profile/
     streak.svg was not rendered today" and nothing was committed.
  2. The schedule was two `:30` crons. GitHub drops scheduled runs under load,
     and :00/:30 are the most contended minutes - the 07:30 UTC (1 PM IST) run
     on 2026-09-25 never started at all, and the 19:30 one started 3h late.

Nothing here touches the network or the working tree: cards are rendered into
a temp dir from a fake calendar, and the run asserts that no dist/ or profile/
output appeared in the repo.

Run: python3 scripts/test_schedule.py
"""

import hashlib
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import generate_cards as gc  # noqa: E402
import slot  # noqa: E402

UTC = timezone.utc
WORKFLOW = ROOT / ".github" / "workflows" / "streak.yml"
CARDS = ("streak", "streak-light", "activity-graph", "activity-graph-light")


def utc(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=UTC)


# --------------------------------------------------------------------------
# slot arithmetic - IST has no DST, so 1 AM IST is always 19:30 UTC
# --------------------------------------------------------------------------

def test_1am_ist_is_the_am_slot_of_the_new_day():
    assert slot.slot_for(utc(2026, 9, 24, 19, 30)) == "2026-09-25 AM"


def test_1pm_ist_is_the_pm_slot():
    assert slot.slot_for(utc(2026, 9, 25, 7, 30)) == "2026-09-25 PM"


def test_a_minute_before_1am_ist_still_belongs_to_yesterdays_pm():
    # 00:59 IST - a PM run that landed very late must not claim tomorrow's AM.
    assert slot.slot_for(utc(2026, 9, 24, 19, 29)) == "2026-09-24 PM"


def test_the_real_late_run_of_2026_09_24_was_the_am_slot():
    # Run 36068172556 started 22:34 UTC = 04:04 IST on the 25th.
    assert slot.slot_for(utc(2026, 9, 24, 22, 34)) == "2026-09-25 AM"


def test_midday_boundary():
    assert slot.slot_for(utc(2026, 9, 25, 7, 29)) == "2026-09-25 AM"


# --------------------------------------------------------------------------
# gate - first run in a window works, the retries behind it no-op
# --------------------------------------------------------------------------

def card_with(slot_id):
    return f"<svg></svg>\n<!-- slot {slot_id} -->\n" if slot_id else "<svg></svg>\n"


def test_gate_runs_when_card_is_from_the_previous_slot():
    now = utc(2026, 9, 25, 7, 37)
    assert slot.should_run("schedule", now, card_with("2026-09-25 AM")) is True


def test_gate_holds_when_this_slot_is_already_done():
    # The retry crons behind the first landed run must not add a third commit.
    now = utc(2026, 9, 25, 8, 47)
    assert slot.should_run("schedule", now, card_with("2026-09-25 PM")) is False


def test_gate_runs_when_card_has_no_slot_stamp():
    # The first run after this change: the old cards predate the stamp.
    now = utc(2026, 9, 25, 7, 37)
    assert slot.should_run("schedule", now, card_with(None)) is True


def test_manual_dispatch_always_runs():
    now = utc(2026, 9, 25, 8, 47)
    assert slot.should_run("workflow_dispatch", now, card_with("2026-09-25 PM")) is True


def test_gate_is_idempotent_across_every_retry_in_a_window():
    done = card_with("2026-09-25 PM")
    for minutes in range(0, 12 * 60 - 31, 17):
        now = utc(2026, 9, 25, 7, 30) + timedelta(minutes=minutes)
        assert slot.should_run("schedule", now, done) is False, now


def test_gate_cli_writes_github_output():
    with tempfile.TemporaryDirectory() as tmp:
        card = Path(tmp) / "streak.svg"
        card.write_text(card_with("2026-09-25 AM"))
        out = Path(tmp) / "gh_output"
        env = {**os.environ, "EVENT": "schedule", "CARD_FILE": str(card),
               "GITHUB_OUTPUT": str(out), "NOW_UTC": "2026-09-25T07:41:00Z"}
        subprocess.run([sys.executable, str(HERE / "slot.py")], env=env, check=True,
                       capture_output=True)
        lines = out.read_text().splitlines()
        assert "run=true" in lines, lines
        assert "slot=2026-09-25 PM" in lines, lines


# --------------------------------------------------------------------------
# render + verify - the exact failure from run 36068172556
# --------------------------------------------------------------------------

def fake_calendar(today):
    return {today - timedelta(days=i): (i % 5) + 1 for i in range(400)}


def render_into(tmp, card_day, card_slot):
    env_before = dict(os.environ)
    os.environ.update(CARD_DAY=card_day, CARD_SLOT=card_slot, CARD_OUT=tmp)
    real = gc.contributions
    gc.contributions = lambda: fake_calendar(date.fromisoformat(card_day))
    gc.OUT_DIR = tmp
    try:
        gc.main()
    finally:
        gc.contributions = real
        gc.OUT_DIR = "dist"
        os.environ.clear()
        os.environ.update(env_before)


def run_verify(tmp, card_day, card_slot):
    env = {**os.environ, "CARD_DAY": card_day, "CARD_SLOT": card_slot, "CARD_OUT": tmp}
    return subprocess.run(["bash", str(HERE / "verify_cards.sh")], env=env,
                          capture_output=True, text=True)


def test_rendered_cards_pass_verify():
    # Red on 2026-09-25: "profile/streak.svg was not rendered today".
    with tempfile.TemporaryDirectory() as tmp:
        render_into(tmp, "2026-09-25", "2026-09-25 PM")
        r = run_verify(tmp, "2026-09-25", "2026-09-25 PM")
        assert r.returncode == 0, r.stdout + r.stderr


def test_every_card_carries_both_stamps():
    with tempfile.TemporaryDirectory() as tmp:
        render_into(tmp, "2026-09-25", "2026-09-25 PM")
        for c in CARDS:
            text = (Path(tmp) / f"{c}.svg").read_text()
            assert "<!-- rendered 2026-09-25 -->" in text, c
            assert "<!-- slot 2026-09-25 PM -->" in text, c
            assert text.rstrip().endswith("</svg>"), c


def test_verify_rejects_a_card_from_yesterday():
    with tempfile.TemporaryDirectory() as tmp:
        render_into(tmp, "2026-09-24", "2026-09-24 PM")
        r = run_verify(tmp, "2026-09-25", "2026-09-25 AM")
        assert r.returncode != 0
        assert "not rendered today" in r.stdout + r.stderr


def test_verify_rejects_a_card_from_another_slot():
    with tempfile.TemporaryDirectory() as tmp:
        render_into(tmp, "2026-09-25", "2026-09-25 AM")
        r = run_verify(tmp, "2026-09-25", "2026-09-25 PM")
        assert r.returncode != 0


def test_two_slots_on_a_quiet_day_still_produce_different_files():
    # Two commits a day even when no contribution landed between 1 AM and 1 PM.
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        render_into(a, "2026-09-25", "2026-09-25 AM")
        render_into(b, "2026-09-25", "2026-09-25 PM")
        assert (Path(a) / "streak.svg").read_text() != (Path(b) / "streak.svg").read_text()


def test_rendering_the_same_slot_twice_is_byte_identical():
    # A retry that re-renders a finished slot must not produce a spurious diff.
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        render_into(a, "2026-09-25", "2026-09-25 PM")
        render_into(b, "2026-09-25", "2026-09-25 PM")
        for c in CARDS:
            assert (Path(a) / f"{c}.svg").read_bytes() == (Path(b) / f"{c}.svg").read_bytes()


# --------------------------------------------------------------------------
# workflow shape
# --------------------------------------------------------------------------

def crons():
    return re.findall(r'cron:\s*"([^"]+)"', WORKFLOW.read_text())


def expand(spec):
    minute, hour = spec.split()[:2]
    return [(int(h), int(m)) for h in hour.split(",") for m in minute.split(",")]


def test_no_cron_sits_on_a_contended_minute():
    for spec in crons():
        for _, m in expand(spec):
            assert m not in (0, 15, 30, 45), spec


def test_each_window_has_several_retry_slots_after_its_target():
    # 1 AM IST = 19:30 UTC, 1 PM IST = 07:30 UTC. Every slot must fall at or
    # after the target (never "early") and within 4h of it.
    times = sorted(t for spec in crons() for t in expand(spec))
    for target in ((19, 30), (7, 30)):
        start = target[0] * 60 + target[1]
        hits = [t for t in times if 0 <= (t[0] * 60 + t[1] - start) % 1440 <= 240]
        assert len(hits) >= 5, (target, hits)
    assert all(slot.slot_for(utc(2026, 9, 25, h, m)) for h, m in times)


def test_workflow_never_commits_to_main():
    # Vinay, 2026-09-25: no generated commits in the repo history. The cards
    # live on a side branch instead; main only ever carries his own work.
    text = WORKFLOW.read_text()
    assert "git-auto-commit-action" not in text
    assert not re.search(r"target_branch:\s*main\b", text)


def test_cards_publish_to_the_analytics_branch_as_the_actions_bot():
    text = WORKFLOW.read_text()
    assert re.search(r"target_branch:\s*analytics\b", text)
    assert "keep_history: false" in text
    assert re.search(r"author:\s*github-actions\[bot\]", text)
    assert re.search(r"committer:\s*github-actions\[bot\]", text)


def test_gate_reads_the_last_card_from_the_analytics_branch():
    assert "origin/analytics:streak.svg" in WORKFLOW.read_text()


def test_only_a_missing_branch_counts_as_first_run():
    # A transient fetch error must fail the job, not read as "no branch yet" -
    # that would empty the gate's input and republish a finished slot.
    text = WORKFLOW.read_text()
    assert "git ls-remote --exit-code --heads origin analytics" in text
    assert "|| echo" not in text
    assert "|| : >" not in text


def test_gate_runs_when_the_analytics_branch_does_not_exist_yet():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "gh_output"
        env = {**os.environ, "EVENT": "schedule", "CARD_FILE": str(Path(tmp) / "missing.svg"),
               "GITHUB_OUTPUT": str(out), "NOW_UTC": "2026-09-25T07:41:00Z"}
        subprocess.run([sys.executable, str(HERE / "slot.py")], env=env, check=True,
                       capture_output=True)
        assert "run=true" in out.read_text().splitlines()


def test_readme_loads_every_card_from_the_analytics_branch():
    readme = (ROOT / "README.md").read_text()
    assert "/main/profile/" not in readme
    for c in CARDS:
        assert f"vinay4955/vinay4955/analytics/{c}.svg" in readme, c


def test_main_history_names_only_the_owner():
    # The Contributors sidebar is built from main. Any bot author or AI
    # co-author trailer puts a second name there.
    log = subprocess.run(
        ["git", "-C", str(ROOT), "log", "HEAD",
         "--format=%an <%ae>%n%(trailers:key=Co-authored-by,valueonly)"],
        capture_output=True, text=True, check=True).stdout
    bad = {l for l in log.splitlines()
           if l.strip() and re.search(r"claude|anthropic|dependabot|\[bot\]", l, re.I)}
    assert not bad, bad


def test_scheduled_workflows_are_kept_alive_without_commits():
    # GitHub disables schedules in a public repo after 60 days without
    # activity, and the cards no longer commit to main. Re-enabling through
    # the API resets that clock without a commit.
    text = WORKFLOW.read_text()
    assert re.search(r"^\s*actions:\s*write", text, re.M)
    for wf in ("streak.yml", "snake.yml"):
        assert f"actions/workflows/{wf}/enable" in text, wf


def test_a_push_that_changes_the_cards_publishes_them_at_once():
    # Otherwise the README points at an analytics branch that does not exist
    # until the next scheduled window.
    text = WORKFLOW.read_text()
    push = text.split("  push:", 1)[1].split("  workflow_dispatch:", 1)[0]
    assert "branches: [main]" in push
    for path in (".github/workflows/streak.yml", "scripts/**", "README.md"):
        assert f"'{path}'" in push, path


def test_workflow_serialises_runs():
    assert re.search(r"^concurrency:", WORKFLOW.read_text(), re.M)


def test_every_step_after_the_gate_is_gated():
    # Dropping the `if:` from the publish step would pass every other test
    # while republishing on every retry cron.
    text = WORKFLOW.read_text()
    for name in ("Render the cards", "Verify the cards are current", "Publish the cards"):
        block = text.split(f"- name: {name}", 1)[1].split("- name:", 1)[0]
        assert "if: steps.gate.outputs.run == 'true'" in block, name


def test_card_day_is_read_from_the_clock_once():
    # Two `date` calls can straddle Berlin midnight and make verify reject a
    # good card.
    assert WORKFLOW.read_text().count("date +%F") == 1


def test_workflow_uses_the_shared_verify_script():
    assert "scripts/verify_cards.sh" in WORKFLOW.read_text()


# --------------------------------------------------------------------------

def tree_digest():
    h = hashlib.sha256()
    for d in ("dist", "profile"):
        for p in sorted((ROOT / d).glob("*")):
            h.update(p.name.encode() + p.read_bytes())
    return h.hexdigest()


if __name__ == "__main__":
    before = tree_digest()
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:  # noqa: BLE001 - report every failure, then exit
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    assert tree_digest() == before, "tests wrote card output into the repo"
    print("isolation: repo tree untouched")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
