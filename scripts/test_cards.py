#!/usr/bin/env python3
"""Tests for the render stamp and the README cache-buster.

Both exist because a card can be correct on the remote and stale on the
profile. GitHub serves README images through Camo, which caches by URL, so an
unchanging URL keeps serving yesterday's picture; and before the stamp, the
"all four cards verified" step actually date-checked only the two graphs.

Run: python3 scripts/test_cards.py
"""

import sys
import tempfile
import xml.dom.minidom as minidom
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import generate_cards as gc  # noqa: E402

DAY = date(2026, 9, 12)
FAILURES: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}: got {got!r}, want {want!r}")
        FAILURES.append(name)


def readme(*urls: str) -> str:
    body = "\n".join(
        f'  <img src="https://raw.githubusercontent.com/vinay4955/vinay4955/main/{u}" />'
        for u in urls
    )
    return f"# profile\n<picture>\n{body}\n</picture>\n"


FOUR = ("profile/streak.svg", "profile/streak-light.svg",
        "profile/activity-graph.svg", "profile/activity-graph-light.svg")


print("stamp marks the card without disturbing it")
svg = '<svg xmlns="http://www.w3.org/2000/svg" width="10">\n  <rect/>\n</svg>'
out = gc.stamp(svg, DAY)
check("stamp lands inside the root element", "<!-- rendered 2026-09-12 -->" in out, True)
check("opening tag is untouched", out.startswith('<svg xmlns="http://www.w3.org/2000/svg" width="10">'), True)
check("body survives", "<rect/>" in out and out.rstrip().endswith("</svg>"), True)

try:
    minidom.parseString(out)
    check("stamped card is still well-formed XML", True, True)
except Exception as e:
    check("stamped card is still well-formed XML", f"parse error: {e}", True)

print("stamp survives a multi-line opening tag (the real templates)")
multi = '<svg xmlns="http://www.w3.org/2000/svg"\n     viewBox="0 0 1 1"\n     role="img">\n  <g/>\n</svg>'
check("comment follows the tag, not the first attribute",
      gc.stamp(multi, DAY).splitlines()[3].strip(), "<!-- rendered 2026-09-12 -->")


def with_readme(text):
    """Run refresh_readme against a throwaway README, return (changed, text)."""
    d = tempfile.mkdtemp()
    path = Path(d) / "README.md"
    path.write_text(text)
    real = gc.README
    gc.README = str(path)
    try:
        changed = gc.refresh_readme(DAY)
    finally:
        gc.README = real
    return changed, path.read_text()


print("README cache-buster")
changed, text = with_readme(readme(*FOUR))
check("a bare README is rewritten", changed, True)
check("every card gets today's buster", text.count("?d=2026-09-12"), 4)

changed, text = with_readme(readme(*(u + "?d=2026-09-11" for u in FOUR)))
check("yesterday's buster is replaced", changed, True)
check("no buster is doubled up", "?d=2026-09-11" in text, False)
check("still exactly four", text.count("?d=2026-09-12"), 4)

changed, _ = with_readme(readme(*(u + "?d=2026-09-12" for u in FOUR)))
check("an already-current README is left alone", changed, False)

print("a README whose markup moved fails loudly rather than going stale")
try:
    with_readme(readme(*FOUR[:3]))
    check("missing card URL exits non-zero", "returned normally", "SystemExit")
except SystemExit as e:
    check("missing card URL exits non-zero", "found 3" in str(e), True)

print("stamp with an XML declaration still lands inside the root")
decl = '<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"><g/></svg>'
out = gc.stamp(decl, DAY)
check("comment is after <svg, not after the declaration",
      out.index("<!-- rendered") > out.index("<svg"), True)
try:
    minidom.parseString(out)
    check("declared card is still well-formed XML", True, True)
except Exception as e:
    check("declared card is still well-formed XML", f"parse error: {e}", True)

print("a card URL that already carries some other query is not mangled")
_, text = with_readme(readme("profile/streak.svg?v=8", *FOUR[1:]))
check("stray ?v= is replaced, not appended", "?v=8" in text, False)
check("still exactly four busters", text.count("?d=2026-09-12"), 4)

print("card filenames with digits or underscores are still matched")
odd = ("profile/streak2.svg", "profile/streak_light.svg",
       "profile/activity-graph.svg", "profile/activity-graph-light.svg")
changed, text = with_readme(readme(*odd))
check("digits and underscores count as cards", changed, True)
check("all four busted", text.count("?d=2026-09-12"), 4)

print("the precheck refuses before any work is done")
d = tempfile.mkdtemp()
path = Path(d) / "README.md"
path.write_text(readme(*FOUR[:2]))
real = gc.README
gc.README = str(path)
try:
    gc.readme_precheck()
    check("precheck rejects a moved README", "returned normally", "SystemExit")
except SystemExit as e:
    check("precheck rejects a moved README", "found 2" in str(e), True)
finally:
    gc.README = real

print("the header card's own ?v= buster is not touched")
head = '<img src="https://raw.githubusercontent.com/vinay4955/vinay4955/main/dark.svg?v=8">\n'
_, text = with_readme(head + readme(*FOUR))
check("header card keeps ?v=8", "dark.svg?v=8" in text, True)

print()
if FAILURES:
    sys.exit(f"{len(FAILURES)} failing: {', '.join(FAILURES)}")
print("all card stamp / README buster tests passed")
