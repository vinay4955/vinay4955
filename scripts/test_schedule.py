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


def all_workflows():
    return sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))


def test_no_workflow_pushes_to_any_branch():
    # 2026-10-17: the repo list showed "Updated 11 hours ago" because GitHub's
    # Updated time is the last push to ANY branch - the snake pushed to
    # `output` and the cards to `analytics` every day. Nothing scheduled may
    # push a branch; publishing goes through a Pages deployment, which is not
    # a git push and leaves pushed_at alone.
    for wf in all_workflows():
        text = wf.read_text()
        assert "ghaction-github-pages" not in text, wf.name
        assert "git-auto-commit-action" not in text, wf.name
        assert not re.search(r"target_branch:", text), wf.name
        assert not re.search(r"git\s+push", text), wf.name
        assert "contents: write" not in text, wf.name


def test_cards_deploy_through_pages_artifact_not_a_branch():
    text = WORKFLOW.read_text()
    assert "actions/upload-pages-artifact@" in text
    assert "actions/deploy-pages@" in text
    assert re.search(r"^\s*pages:\s*write", text, re.M)
    assert re.search(r"^\s*id-token:\s*write", text, re.M)
    assert "name: github-pages" in text


def test_the_deploy_job_only_runs_when_the_gate_says_run():
    text = WORKFLOW.read_text()
    deploy = text.split("\n  deploy:", 1)[1]
    assert "needs: streak" in deploy
    assert "needs.streak.outputs.run == 'true'" in deploy


def test_snake_is_built_into_the_same_deployment():
    # A Pages deployment replaces the whole site, so the snake must ride along
    # in dist/ or it would vanish on the next card refresh.
    assert "Platane/snk@" in WORKFLOW.read_text()
    assert not (ROOT / ".github" / "workflows" / "snake.yml").exists()


def test_gate_reads_the_last_card_from_the_live_pages_site():
    text = WORKFLOW.read_text()
    assert "scripts/fetch_last_card.sh" in text
    assert "github.io" in text
    assert "origin/analytics" not in text


def _fetch(url, out):
    return subprocess.run(["bash", str(HERE / "fetch_last_card.sh"), url, str(out)],
                          capture_output=True, text=True)


class _Site:
    """Local http server serving `routes` {path: (status, body)}."""

    def __init__(self, routes):
        self.routes = routes

    def __enter__(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        routes = self.routes

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                status, body = routes.get(self.path.split("?")[0], (404, b""))
                self.send_response(status)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}"
        return self

    def __exit__(self, *a):
        self.srv.shutdown()


def test_fetch_writes_the_published_card_on_200():
    with tempfile.TemporaryDirectory() as tmp, \
            _Site({"/streak.svg": (200, b"<svg/><!-- slot 2026-10-17 AM -->")}) as site:
        out = Path(tmp) / "last.svg"
        r = _fetch(site.base + "/streak.svg", out)
        assert r.returncode == 0, r.stderr
        assert slot.slot_in(out.read_text()) == "2026-10-17 AM"


def test_fetch_treats_404_as_first_publish():
    with tempfile.TemporaryDirectory() as tmp, _Site({}) as site:
        out = Path(tmp) / "last.svg"
        r = _fetch(site.base + "/streak.svg", out)
        assert r.returncode == 0, r.stderr
        assert out.read_text() == ""


def test_fetch_fails_loudly_on_a_server_error():
    # A 5xx must not read as "nothing published yet" - that would empty the
    # gate's input and republish a slot that is already done.
    with tempfile.TemporaryDirectory() as tmp, \
            _Site({"/streak.svg": (503, b"down")}) as site:
        r = _fetch(site.base + "/streak.svg", Path(tmp) / "last.svg")
        assert r.returncode != 0


def test_fetch_fails_loudly_when_the_site_is_unreachable():
    with tempfile.TemporaryDirectory() as tmp:
        r = _fetch("http://127.0.0.1:9/streak.svg", Path(tmp) / "last.svg")
        assert r.returncode != 0


def test_fetch_is_idempotent():
    with tempfile.TemporaryDirectory() as tmp, \
            _Site({"/streak.svg": (200, b"<svg/>")}) as site:
        out = Path(tmp) / "last.svg"
        _fetch(site.base + "/streak.svg", out)
        first = out.read_bytes()
        _fetch(site.base + "/streak.svg", out)
        assert out.read_bytes() == first


def test_gate_runs_when_no_card_has_been_published_yet():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "gh_output"
        env = {**os.environ, "EVENT": "schedule", "CARD_FILE": str(Path(tmp) / "missing.svg"),
               "GITHUB_OUTPUT": str(out), "NOW_UTC": "2026-09-25T07:41:00Z"}
        subprocess.run([sys.executable, str(HERE / "slot.py")], env=env, check=True,
                       capture_output=True)
        assert "run=true" in out.read_text().splitlines()


def test_readme_loads_every_card_from_the_pages_site():
    readme = (ROOT / "README.md").read_text()
    assert "/main/profile/" not in readme
    assert "/analytics/" not in readme
    for c in CARDS:
        assert f"https://vinay4955.github.io/vinay4955/{c}.svg" in readme, c


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
    assert "actions/workflows/streak.yml/enable" in text
    assert "snake.yml" not in text


def test_a_push_that_changes_the_cards_publishes_them_at_once():
    # Otherwise the README points at a Pages site that has no cards
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
    for name in ("Render the cards", "Verify the cards are current",
                 "Build the snake", "Upload the Pages artifact"):
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
