#!/usr/bin/env python3
"""
Render the GitHub Analytics cards under profile/ from GitHub's own GraphQL API.

WHY THIS EXISTS
---------------
These cards used to be fetched, already-rendered, from third-party hosts:
  - the streak card  -> a third-party Action
  - the activity graph -> github-readme-activity-graph.vercel.app

On 2026-08-24 that Vercel app started answering 402 "DEPLOYMENT_DISABLED" (the
maintainer's hobby deployment was switched off). The old workflow caught the
failed download and *kept the previous file*, so every nightly run went green
while quietly serving a graph that was frozen for 13 days. The streak card,
which was generated locally, kept updating - which is exactly why the two
cards drifted apart and the breakage was invisible.

The fix is to depend on nothing that anyone else has to keep paying for.
Everything below is rendered here, from stdlib only, out of the contribution
calendar that GitHub itself serves. There is no runtime service to go down.

If the data cannot be fetched this script EXITS NON-ZERO and writes nothing.
Never re-introduce a fallback that silently preserves a stale card - a stale
card that looks fine is precisely the failure this replaced.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

USER = os.environ.get("CARD_USER", "vinay4955")
OUT_DIR = os.environ.get("CARD_OUT", "profile")
API = "https://api.github.com/graphql"
GRAPH_DAYS = 365


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------

def gql(query: str, variables: dict) -> dict:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        sys.exit("FATAL: no GITHUB_TOKEN/GH_TOKEN in the environment")

    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        API,
        data=body,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": f"{USER}-profile-cards",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)

    # A GraphQL error arrives as HTTP 200 with an "errors" key. Treating that as
    # success is how you end up writing an empty card over a good one.
    if "errors" in payload:
        sys.exit(f"FATAL: GraphQL errors: {json.dumps(payload['errors'])}")
    return payload["data"]


def account_created() -> date:
    data = gql("query($login:String!){ user(login:$login){ createdAt } }", {"login": USER})
    return datetime.strptime(data["user"]["createdAt"], "%Y-%m-%dT%H:%M:%SZ").date()


def contributions() -> dict[date, int]:
    """Every contribution day for the life of the account.

    The calendar API caps each query at one year, so walk year-sized windows
    from account creation to today and merge them.
    """
    query = """
    query($login:String!, $from:DateTime!, $to:DateTime!) {
      user(login:$login) {
        contributionsCollection(from:$from, to:$to) {
          contributionCalendar {
            weeks { contributionDays { date contributionCount } }
          }
        }
      }
    }
    """
    days: dict[date, int] = {}
    today = date.today()
    cursor = account_created()

    while cursor <= today:
        window_end = min(date(cursor.year, 12, 31), today)
        data = gql(query, {
            "login": USER,
            "from": f"{cursor.isoformat()}T00:00:00Z",
            "to": f"{window_end.isoformat()}T23:59:59Z",
        })
        cal = data["user"]["contributionsCollection"]["contributionCalendar"]
        for week in cal["weeks"]:
            for day in week["contributionDays"]:
                d = date.fromisoformat(day["date"])
                if cursor <= d <= window_end:
                    days[d] = day["contributionCount"]
        cursor = window_end + timedelta(days=1)

    if not days:
        sys.exit("FATAL: the contribution calendar came back empty")
    return days


def summarise(days: dict[date, int]) -> dict:
    today = date.today()
    active = sorted(d for d, n in days.items() if n > 0)
    if not active:
        # The calendar API returns every date in range, zeros included, so an
        # empty `active` is NOT an empty response - it means the account shows
        # no activity at all for its whole life. On an established account that
        # far more likely means the token lost visibility than that the history
        # really vanished, and rendering 0/0/0 over a good card is precisely the
        # write-plausible-nonsense failure this script exists to prevent. Stop.
        sys.exit("FATAL: calendar returned zero activity for the entire account "
                 "lifetime - refusing to overwrite the cards; check token scope")

    # Current streak: consecutive active days ending today. A quiet *today* does
    # not break it - the day is not over yet - so fall back to yesterday.
    anchor = today if days.get(today, 0) > 0 else today - timedelta(days=1)
    cur_end = anchor if days.get(anchor, 0) > 0 else None
    cur_start, cur_len = cur_end, 0
    if cur_end:
        probe = cur_end
        while days.get(probe, 0) > 0:
            cur_start, cur_len = probe, cur_len + 1
            probe -= timedelta(days=1)

    # Longest streak over the whole history.
    best_len, best_start, best_end = 0, None, None
    run_len, run_start = 0, None
    for i, d in enumerate(active):
        if i and (d - active[i - 1]).days == 1:
            run_len += 1
        else:
            run_len, run_start = 1, d
        if run_len > best_len:
            best_len, best_start, best_end = run_len, run_start, d

    return {
        "total": sum(days.values()),
        "first": active[0],
        "current": cur_len,
        "current_start": cur_start,
        "current_end": cur_end,
        "longest": best_len,
        "longest_start": best_start,
        "longest_end": best_end,
        "series": [(today - timedelta(days=i), days.get(today - timedelta(days=i), 0))
                   for i in range(GRAPH_DAYS - 1, -1, -1)],
    }


# --------------------------------------------------------------------------
# themes
# --------------------------------------------------------------------------
# Tokyo Night / GitHub Light, matched to the header card so the whole profile
# reads as one design rather than three borrowed widgets.

THEMES = {
    "dark": {
        "bg": "#1A1B27", "ring": "#BF91F3", "fire": "#BF91F3",
        "big": "#E4E2E2", "side": "#E4E2E2", "label": "#BF91F3",
        "side_label": "#E4E2E2", "date": "#8B949E", "divider": "#E4E2E2",
        "line": "#70A5FD", "point": "#BF91F3", "area_from": "#70A5FD",
        "grid": "#2A2C3D", "axis": "#8B949E", "title": "#38BDAE",
    },
    "light": {
        "bg": "#FFFEFE", "ring": "#FB8C00", "fire": "#FB8C00",
        "big": "#151515", "side": "#151515", "label": "#FB8C00",
        "side_label": "#151515", "date": "#464646", "divider": "#DDDDDD",
        "line": "#4C71F2", "point": "#1F2328", "area_from": "#4C71F2",
        "grid": "#E7E9EB", "axis": "#57606A", "title": "#1F2328",
    },
}

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fmt_day(d: date, with_year: bool) -> str:
    return f"{MONTHS[d.month - 1]} {d.day}" + (f", {d.year}" if with_year else "")


def fmt_range(start: date | None, end: date | None) -> str:
    if not start or not end:
        return ""
    this_year = date.today().year
    if start == end:
        return fmt_day(start, start.year != this_year)
    show_year = start.year != this_year or end.year != this_year
    if start.year != end.year:
        return f"{fmt_day(start, True)} - {fmt_day(end, True)}"
    return f"{fmt_day(start, show_year)} - {fmt_day(end, show_year)}"


# --------------------------------------------------------------------------
# streak card
# --------------------------------------------------------------------------

def render_streak(s: dict, t: dict) -> str:
    """Three-panel streak card.

    RENDERING CONTRACT: every element's BASE state is fully visible. The
    animations below are decoration layered on top. Do not move a value behind
    `opacity: 0` or a zero-size clip - GitHub proxies these SVGs through Camo
    and its mobile apps rasterise without running CSS, which turns any
    animation-gated content into a blank card. This repo has shipped that bug
    once already.
    """
    total = f"{s['total']:,}"
    total_range = f"{fmt_day(s['first'], True)} - Present"
    cur_range = fmt_range(s["current_start"], s["current_end"]) or "-"
    long_range = fmt_range(s["longest_start"], s["longest_end"]) or "-"
    font = "'Segoe UI', Ubuntu, sans-serif"

    def column(x: int, num: str, label: str, sub: str) -> str:
        return f"""
  <g>
    <text x="{x}" y="88" font-family="{font}" font-weight="700" font-size="28"
          fill="{t['side']}" text-anchor="middle">{num}</text>
    <text x="{x}" y="118" font-family="{font}" font-weight="400" font-size="14"
          fill="{t['side_label']}" text-anchor="middle">{label}</text>
    <text x="{x}" y="145" font-family="{font}" font-weight="400" font-size="12"
          fill="{t['date']}" text-anchor="middle">{sub}</text>
  </g>"""

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="495" height="195"
     viewBox="0 0 495 195" role="img"
     aria-label="GitHub streak: {total} total contributions since {fmt_day(s['first'], True)}, current streak {s['current']} days, longest streak {s['longest']} days">
  <defs>
    <mask id="ring-gap">
      <rect width="495" height="195" fill="white"/>
      <ellipse cx="247.5" cy="36" rx="15" ry="12" fill="black"/>
    </mask>
  </defs>

  <rect width="495" height="195" rx="10" fill="{t['bg']}"/>
  <line x1="165" y1="32" x2="165" y2="163" stroke="{t['divider']}" stroke-opacity="0.22"/>
  <line x1="330" y1="32" x2="330" y2="163" stroke="{t['divider']}" stroke-opacity="0.22"/>
{column(82, total, 'Total Contributions', total_range)}
{column(412, str(s['longest']), 'Longest Streak', long_range)}

  <g>
    <circle cx="247.5" cy="70" r="34" fill="none" stroke="{t['ring']}"
            stroke-width="5" mask="url(#ring-gap)"/>
    <path d="M247.5 24 c -2.4 4.6 -7.1 7.1 -7.1 12.5 c 0 4.4 3.2 7.7 7.1 7.7
             c 3.9 0 7.1 -3.3 7.1 -7.7 c 0 -5.4 -4.7 -7.9 -7.1 -12.5 z"
          fill="{t['fire']}"/>
  </g>
  <g>
    <text x="247.5" y="82" font-family="{font}" font-weight="700" font-size="30"
          fill="{t['big']}" text-anchor="middle">{s['current']}</text>
    <text x="247.5" y="130" font-family="{font}" font-weight="700" font-size="14"
          fill="{t['label']}" text-anchor="middle">Current Streak</text>
    <text x="247.5" y="152" font-family="{font}" font-weight="400" font-size="12"
          fill="{t['date']}" text-anchor="middle">{cur_range}</text>
  </g>
</svg>
"""


# --------------------------------------------------------------------------
# activity graph
# --------------------------------------------------------------------------

W, H = 1200, 420
PAD_L, PAD_R, PAD_T, PAD_B = 70, 34, 74, 58
PLOT_W = W - PAD_L - PAD_R
PLOT_H = H - PAD_T - PAD_B


def smooth_path(pts: list[tuple[float, float]]) -> str:
    """Catmull-Rom through the points, emitted as cubic beziers.

    A straight polyline over 365 daily values reads as noise; the spline keeps
    the shape legible without inventing peaks that are not in the data.
    """
    if len(pts) < 2:
        return f"M {pts[0][0]:.2f} {pts[0][1]:.2f}" if pts else ""
    d = [f"M {pts[0][0]:.2f} {pts[0][1]:.2f}"]
    for i in range(len(pts) - 1):
        p0 = pts[i - 1] if i else pts[0]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < len(pts) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d.append(f"C {c1[0]:.2f} {c1[1]:.2f}, {c2[0]:.2f} {c2[1]:.2f}, {p2[0]:.2f} {p2[1]:.2f}")
    return " ".join(d)


def render_graph(s: dict, t: dict, theme_name: str) -> str:
    series = s["series"]
    peak = max(n for _, n in series) or 1
    # Round the axis top to something human before scaling.
    step = 1 if peak <= 5 else 2 if peak <= 10 else 5 if peak <= 25 else 10 if peak <= 60 else 25
    top = ((peak + step - 1) // step) * step
    font = "'Segoe UI', Ubuntu, sans-serif"

    def px(i: int) -> float:
        return PAD_L + (i / (len(series) - 1)) * PLOT_W

    def py(n: int) -> float:
        return PAD_T + PLOT_H - (n / top) * PLOT_H

    pts = [(px(i), py(n)) for i, (_, n) in enumerate(series)]
    line = smooth_path(pts)
    area = f"{line} L {pts[-1][0]:.2f} {PAD_T + PLOT_H} L {pts[0][0]:.2f} {PAD_T + PLOT_H} Z"

    # Horizontal grid + y labels.
    grid = []
    ticks = top // step if top // step <= 6 else 5
    for k in range(ticks + 1):
        val = round(top * k / ticks)
        y = py(val)
        grid.append(
            f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{PAD_L + PLOT_W}" y2="{y:.1f}" '
            f'stroke="{t["grid"]}" stroke-width="1"/>'
            f'<text x="{PAD_L - 14}" y="{y + 4:.1f}" font-family="{font}" font-size="13" '
            f'fill="{t["axis"]}" text-anchor="end">{val}</text>')

    # Month labels on the first day of each month present in the window.
    months, seen = [], set()
    for i, (d, _) in enumerate(series):
        key = (d.year, d.month)
        if key in seen:
            continue
        seen.add(key)
        if i < 6 or i > len(series) - 6:
            continue
        months.append(
            f'<text x="{px(i):.1f}" y="{PAD_T + PLOT_H + 28}" font-family="{font}" '
            f'font-size="13" fill="{t["axis"]}" text-anchor="middle">'
            f'{MONTHS[d.month - 1]} {str(d.year)[2:]}</text>')

    start, end = series[0][0], series[-1][0]
    total = sum(n for _, n in series)

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}"
     viewBox="0 0 {W} {H}" role="img"
     aria-label="Contribution activity graph: {total} contributions between {start} and {end}, peaking at {peak} in a day">
  <style>
    @keyframes draw {{ from {{ stroke-dasharray: 7000; stroke-dashoffset: 7000 }}
                       to   {{ stroke-dasharray: 7000; stroke-dashoffset: 0 }} }}
    .ln {{ animation: draw 2.2s ease-out }}
  </style>
  <defs>
    <linearGradient id="fill-{theme_name}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%"   stop-color="{t['area_from']}" stop-opacity="0.45"/>
      <stop offset="100%" stop-color="{t['area_from']}" stop-opacity="0.02"/>
    </linearGradient>
  </defs>

  <rect width="{W}" height="{H}" rx="10" fill="{t['bg']}"/>

  <text x="{PAD_L - 14}" y="40" font-family="{font}" font-weight="600" font-size="20"
        fill="{t['title']}">Contribution Graph</text>
  <text x="{W - PAD_R}" y="40" font-family="{font}" font-size="14"
        fill="{t['axis']}" text-anchor="end">{total:,} contributions in the last year</text>

  {''.join(grid)}
  <path d="{area}" fill="url(#fill-{theme_name})"/>
  <path class="ln" d="{line}" fill="none" stroke="{t['line']}" stroke-width="2.4"
        stroke-linecap="round" stroke-linejoin="round"/>
  {''.join(months)}
</svg>
"""


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> None:
    summary = summarise(contributions())
    os.makedirs(OUT_DIR, exist_ok=True)

    outputs = {
        "streak.svg":             render_streak(summary, THEMES["dark"]),
        "streak-light.svg":       render_streak(summary, THEMES["light"]),
        "activity-graph.svg":     render_graph(summary, THEMES["dark"], "dark"),
        "activity-graph-light.svg": render_graph(summary, THEMES["light"], "light"),
    }
    for name, svg in outputs.items():
        with open(os.path.join(OUT_DIR, name), "w") as fh:
            fh.write(svg)

    print(f"total={summary['total']} current={summary['current']} "
          f"longest={summary['longest']} first={summary['first']}")
    print("wrote: " + ", ".join(outputs))


if __name__ == "__main__":
    main()
