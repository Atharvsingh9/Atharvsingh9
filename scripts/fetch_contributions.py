#!/usr/bin/env python3
"""
Fetch real daily contribution counts from GitHub's public, unauthenticated
contributions endpoint (the same fragment the profile page itself uses) and
write data/contributions.json with the raw days plus derived stats
(current streak, longest streak, best day, monthly totals).

No token, no auth, no GraphQL -- just the public HTML GitHub already serves.
Run daily by .github/workflows/update-profile-art.yml.

Robustness: each day's count is read from GitHub's <tool-tip> element (matched
to the cell by id). If tooltips are missing or their wording changes, we fall
back to the cell's data-level attribute so active days are never silently
reported as zero (keeps the streak honest instead of zeroing it).

Debugging: the HTTP exchange is logged in detail (method, URL, headers,
status, redirects, content-type, length, first 1000 chars). When the response
is HTML it is saved to debug/github_response.html so any GitHub markup change
can be inspected and the parser updated.
"""
import datetime
import json
import os
import re
import sys
import traceback

import requests
from bs4 import BeautifulSoup

USERNAME = os.environ.get("GH_PROFILE_USER", "Atharvsingh9")
URL = f"https://github.com/users/{USERNAME}/contributions"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(HERE, "..", "data", "contributions.json")
DEBUG_DIR = os.path.join(HERE, "..", "debug")
DEBUG_HTML = os.path.join(DEBUG_DIR, "github_response.html")
HEADERS = {
    "User-Agent": "profile-readme-bot/1.0 (+https://github.com/{})".format(USERNAME),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Ordered list of CSS selectors that identify one day cell on the calendar.
# The first selector that matches anything wins; if GitHub renames a class,
# the fallbacks keep the parser working.
CELL_SELECTORS = [
    "td.ContributionCalendar-day[data-date]",
    "td[data-date][data-level]",
    "[data-date][data-level]",
]

NO_CONTRIB_RE = re.compile(r"no contributions", re.I)
COUNT_RE = re.compile(r"(\d+)\s+contribution", re.I)


def _save_html(body):
    """Persist the raw GitHub HTML so markup changes can be inspected."""
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        with open(DEBUG_HTML, "w", encoding="utf-8") as f:
            f.write(body)
        print(f"[fetch] saved response HTML to {DEBUG_HTML}")
    except OSError as e:
        print(f"[fetch] could not save debug HTML: {e}", file=sys.stderr)


def cell_count(soup, td):
    """Best-effort daily count for a single calendar cell.

    Signal priority:
      1. <tool-tip for="{td id}"> text, e.g. "13 contributions on July 19th."
      2. aria-label on the cell itself
      3. data-level attribute (level > 0 means at least one contribution)
    """
    td_id = td.get("id")
    text = ""
    if td_id:
        tooltip = soup.find("tool-tip", attrs={"for": td_id})
        if tooltip is not None:
            text = tooltip.get_text(" ", strip=True)
    if not text:
        text = td.get("aria-label") or ""

    if NO_CONTRIB_RE.search(text):
        return 0
    m = COUNT_RE.search(text)
    if m:
        return int(m.group(1))
    m = re.match(r"(\d+)", text)
    if m:
        return int(m.group(1))

    # Tooltip/aria missing or unparseable -- fall back to the level attribute.
    lvl = td.get("data-level")
    if lvl is not None:
        print(f"  tooltip missing for {td.get('data-date')} (level={lvl}); "
              "using level fallback", file=sys.stderr)
        try:
            return 1 if int(lvl) > 0 else 0
        except ValueError:
            pass
    return 0


def extract_days(soup):
    """Pull every day cell out of the parsed contributions page."""
    cells = []
    for selector in CELL_SELECTORS:
        cells = soup.select(selector)
        if cells:
            print(f"[fetch] selected cells with '{selector}' ({len(cells)} cells)")
            break
    if not cells:
        return []

    days = []
    for td in cells:
        date = td.get("data-date")
        if not date:
            continue
        days.append({"date": date, "count": cell_count(soup, td)})
    days.sort(key=lambda d: d["date"])
    return days


def fetch_days():
    """Perform the HTTP GET with full diagnostics and parse the response."""
    print(f"[fetch] GET {URL}")
    print(f"[fetch] headers: {HEADERS}")
    try:
        resp = requests.get(URL, headers=HEADERS, timeout=30, allow_redirects=True)
    except requests.RequestException as e:
        print("[fetch] REQUEST FAILED -- could not reach the GitHub endpoint.", file=sys.stderr)
        print(f"[fetch]   error: {type(e).__name__}: {e}", file=sys.stderr)
        print("[fetch]   fix: check network access from this machine/runner. If this "
              "happens in GitHub Actions, ensure the runner has internet and try again.",
              file=sys.stderr)
        raise

    print(f"[fetch] status: {resp.status_code}")
    print(f"[fetch] redirects: {[h.status_code for h in resp.history]}")
    print(f"[fetch] final URL: {resp.url}")
    print(f"[fetch] content-type: {resp.headers.get('Content-Type')}")
    print(f"[fetch] response length: {len(resp.content)} bytes")
    print("[fetch] response head:")
    print(resp.text[:1000])
    print("[fetch] --------------------------------------------------")

    if resp.status_code == 404:
        print(f"[fetch] ERROR: GitHub returned 404 -- user '{USERNAME}' does not "
              "exist or the URL is wrong.", file=sys.stderr)
        print("[fetch] fix: check GH_PROFILE_USER / the username passed to the "
              "script.", file=sys.stderr)
        resp.raise_for_status()
    if not resp.ok:
        print(f"[fetch] ERROR: GitHub returned HTTP {resp.status_code}.", file=sys.stderr)
        print(f"[fetch] fix: inspect the response above (saved to {DEBUG_HTML} if "
              "HTML) and retry after a rate-limit window if applicable.",
              file=sys.stderr)
        resp.raise_for_status()

    content_type = resp.headers.get("Content-Type", "")
    if "html" in content_type.lower():
        _save_html(resp.text)

    soup = BeautifulSoup(resp.text, "html.parser")
    days = extract_days(soup)
    if not days:
        print("[fetch] ERROR: no calendar day cells found in the response.", file=sys.stderr)
        print(f"[fetch] GitHub may have changed its contribution page markup.", file=sys.stderr)
        print(f"[fetch] The raw HTML was saved to {DEBUG_HTML}; open it, find the "
              "day-cell selector, and update CELL_SELECTORS / cell_count.",
              file=sys.stderr)
        sys.exit(1)

    total = sum(d["count"] for d in days)
    active = sum(1 for d in days if d["count"] > 0)
    print(f"[fetch] parsed {len(days)} days ({days[0]['date']} .. {days[-1]['date']}): "
          f"total {total}, active {active}")
    return days


def validate_days(days):
    """Sanity-check parsed days before they are written to disk."""
    if not days:
        raise ValueError("day list is empty")
    seen = set()
    for d in days:
        date = d["date"]
        try:
            datetime.date.fromisoformat(date)
        except ValueError:
            raise ValueError(f"invalid date {date!r}")
        if date in seen:
            raise ValueError(f"duplicate date {date}")
        seen.add(date)
        count = d["count"]
        if not isinstance(count, int) or count < 0:
            raise ValueError(f"invalid count {count!r} for {date}")
    if (datetime.date.fromisoformat(days[-1]["date"])
            - datetime.date.fromisoformat(days[0]["date"])) >= datetime.timedelta(days=400):
        raise ValueError("date range spans more than ~1 year; something is wrong")


def compute_current_streak(days):
    if not days:
        return 0, None, None
    idx = len(days) - 1
    if days[idx]["count"] == 0:
        idx -= 1  # today isn't over yet -- don't break the streak on it
    streak = 0
    end_idx = idx
    while idx >= 0 and days[idx]["count"] > 0:
        streak += 1
        idx -= 1
    start_idx = idx + 1
    if streak == 0:
        return 0, None, None
    return streak, days[start_idx]["date"], days[end_idx]["date"]


def compute_longest_streak(days):
    longest = run = 0
    longest_start = longest_end = None
    run_start_idx = None
    for i, d in enumerate(days):
        if d["count"] > 0:
            if run == 0:
                run_start_idx = i
            run += 1
            if run > longest:
                longest = run
                longest_start = days[run_start_idx]["date"]
                longest_end = days[i]["date"]
        else:
            run = 0
    return longest, longest_start, longest_end


def build_data(days):
    if not days:
        raise ValueError("cannot build data from an empty day list")
    total = sum(d["count"] for d in days)
    active_days = sum(1 for d in days if d["count"] > 0)
    best = max(days, key=lambda d: d["count"])
    cur_len, cur_start, cur_end = compute_current_streak(days)
    long_len, long_start, long_end = compute_longest_streak(days)

    monthly = {}
    for d in days:
        key = d["date"][:7]
        monthly[key] = monthly.get(key, 0) + d["count"]
    monthly_list = [{"month": k, "total": v} for k, v in sorted(monthly.items())]

    return {
        "username": USERNAME,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "range": {"start": days[0]["date"], "end": days[-1]["date"]},
        "total_contributions": total,
        "active_days": active_days,
        "avg_per_active_day": round(total / active_days, 1) if active_days else 0,
        "current_streak": {"length": cur_len, "start": cur_start, "end": cur_end},
        "longest_streak": {"length": long_len, "start": long_start, "end": long_end},
        "best_day": {"date": best["date"], "count": best["count"]},
        "monthly": monthly_list,
        "days": days,
    }


def parse_contributions_html(text):
    """Reusable parser: raw HTML -> list of {'date', 'count'} days.

    Used by fetch_contributions.py itself and by generate_streak_svg.py as a
    standalone fallback so neither script depends on a third-party API.
    """
    soup = BeautifulSoup(text, "html.parser")
    return extract_days(soup)


def main():
    try:
        days = fetch_days()
        validate_days(days)
        data = build_data(days)
    except requests.RequestException as e:
        print(f"[fetch] request failed: {e}", file=sys.stderr)
        sys.exit(1)
    except (ValueError, KeyError) as e:
        print(f"[fetch] bad data: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"[fetch] unexpected error: {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    try:
        with open(OUT_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError as e:
        print(f"[fetch] could not write {OUT_PATH}: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"[fetch] wrote {OUT_PATH}: {data['total_contributions']} contributions, "
          f"current streak {data['current_streak']['length']} "
          f"({data['current_streak']['start']}..{data['current_streak']['end']}), "
          f"longest streak {data['longest_streak']['length']}")


if __name__ == "__main__":
    main()
