#!/usr/bin/env python3
"""Generate an animated GitHub-streak SVG (squares light up one by one).

Works standalone; designed to run in a GitHub Action daily to stay live.

Data source priority:
  1. data/contributions.json -- written by scripts/fetch_contributions.py straight
     from GitHub's own public contribution page (the exact numbers GitHub shows).
     This is the primary, authoritative source and is always fresh in CI because
     the fetch step runs first.
  2. A direct scrape of https://github.com/users/<user>/contributions via the
     parser in fetch_contributions.py. Used only when the local snapshot is
     missing (e.g. running standalone on a fresh checkout). There is no
     dependency on any third-party contribution API.

The streak stats (current / longest / best day) are derived from the daily
counts locally so they can never drift from the rendered graph.

Usage: python generate_streak_svg.py [username] [output.svg]
"""
import sys, json, os, datetime, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LOCAL = os.path.join(HERE, "..", "data", "contributions.json")

if len(sys.argv) > 1:
    USER = sys.argv[1]
else:
    USER = os.environ.get("GH_PROFILE_USER", "Atharvsingh9")
OUT = sys.argv[2] if len(sys.argv) > 2 else "streak.svg"


def level_for(count):
    if count <= 0:
        return 0
    if count <= 4:
        return 1
    if count <= 9:
        return 2
    if count <= 19:
        return 3
    return 4


def normalize_days(data):
    """Accept either our scrape schema or the legacy API schema."""
    if isinstance(data, dict) and isinstance(data.get("days"), list):
        raw = data["days"]
    elif isinstance(data, dict) and isinstance(data.get("contributions"), list):
        raw = data["contributions"]
    else:
        raise ValueError("unrecognized contribution data schema")

    days = []
    for d in raw:
        date = d.get("date")
        if not date:
            continue
        try:
            count = int(d.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        try:
            level = int(d.get("level") or level_for(count))
        except (TypeError, ValueError):
            level = level_for(count)
        days.append({"date": date, "count": count, "level": level})
    days.sort(key=lambda d: d["date"])
    return days


def scrape_github(user):
    """Direct scrape fallback using the parser from fetch_contributions.py.

    Only reached when data/contributions.json is missing/unusable, so the
    script still works standalone without any third-party API.
    """
    sys.path.insert(0, HERE)
    try:
        import fetch_contributions as fc
    except ImportError as e:
        raise SystemExit(
            f"[streak] cannot scrape fallback: fetch_contributions.py missing ({e}). "
            f"Fix: ensure scripts/fetch_contributions.py exists and that "
            f"requests + beautifulsoup4 are installed (pip install -r "
            f"scripts/requirements.txt)."
        )

    url = f"https://github.com/users/{user}/contributions"
    print(f"[streak] scraping {url}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": fc.HEADERS["User-Agent"]})
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8", "replace")
        print(f"[streak] scrape status {r.status}, {len(body)} bytes")
    except Exception as e:
        raise SystemExit(f"[streak] scrape failed for {user}: {type(e).__name__}: {e}")

    days = fc.parse_contributions_html(body)
    if not days:
        raise SystemExit(
            f"[streak] scrape of {url} returned no calendar cells. GitHub may "
            f"have changed its markup. The raw HTML can be inspected via "
            f"scripts/fetch_contributions.py which saves it to debug/github_response.html."
        )
    return normalize_days({"days": days})


def get_data(user):
    if os.path.exists(LOCAL):
        try:
            with open(LOCAL, encoding="utf-8") as f:
                data = json.load(f)
            days = normalize_days(data)
            if days:
                print(f"[streak] using local snapshot {LOCAL} ({len(days)} days)")
                return days
        except Exception as e:
            print(f"[streak] local snapshot unusable ({e}); scraping GitHub",
                  file=sys.stderr)

    return scrape_github(user)


def compute_stats(days):
    total = sum(d["count"] for d in days)
    active = sum(1 for d in days if d["count"] > 0)

    # current streak: walk backwards from the most recent day; ignore a trailing
    # zero because today may not be over yet.
    idx = len(days) - 1
    if days and days[idx]["count"] == 0:
        idx -= 1
    cur = 0
    while idx >= 0 and days[idx]["count"] > 0:
        cur += 1
        idx -= 1

    longest = run = 0
    for d in days:
        if d["count"] > 0:
            run += 1
            if run > longest:
                longest = run
        else:
            run = 0

    best = max(days, key=lambda d: (d["count"], d["date"])) if days else None
    return total, active, cur, longest, best


contribs = get_data(USER)
total, active, cur, longest, best = compute_stats(contribs)
print(f"[streak] parsed {len(contribs)} days: total {total}, active {active}, "
      f"current streak {cur}, longest {longest}, best {best['count'] if best else 0} on {best['date'] if best else 'n/a'}")

# ---- layout ----
CELL, GAP, RAD, LEFT, TOP = 13, 3, 2.5, 34, 24
COLORS = ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"]
FLASH = "#b4ffaa"
GRAY = "#7d8590"
GREEN = "#39d353"
MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

n = len(contribs)
NW = (n + 6) // 7
W = LEFT + NW*(CELL+GAP) + 6
H = TOP + 7*(CELL+GAP) + 44   # +22 px for the extra streak footer line

# timing (seconds)
REVEAL, DUR = 3.6, 0.55
maxorder = (NW-1) + 6*0.55

rects, labels = [], []
sd = datetime.date.fromisoformat(contribs[0]["date"])
last_m = None
for wk in range(NW):
    d = sd + datetime.timedelta(days=wk*7)
    if d.month != last_m:
        last_m = d.month
        labels.append(f'<text class="lbl" x="{LEFT+wk*(CELL+GAP)}" y="{TOP-8}">{MONTHS[d.month-1]}</text>')
for name, r in [("Mon",1),("Wed",3),("Fri",5)]:
    labels.append(f'<text class="lbl" x="2" y="{TOP+r*(CELL+GAP)+CELL-2}">{name}</text>')

for i, c in enumerate(contribs):
    wk, row, lvl = i//7, i%7, c["level"]
    x = LEFT + wk*(CELL+GAP); y = TOP + row*(CELL+GAP)
    delay = round((wk + row*0.55)/maxorder * REVEAL, 3)
    cls = "c g" if lvl >= 1 else "c e"
    rects.append(
        f'<rect class="{cls}" x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="{RAD}" '
        f'fill="{COLORS[lvl]}" style="animation-delay:{delay}s"/>'
    )

best_txt = f'<tspan fill="{GRAY}">   &#183;   best </tspan><tspan fill="{GREEN}">{best["count"]:,}</tspan><tspan fill="{GRAY}"> on {best["date"]}</tspan>' if best else ""

svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif">
<style>
  text.lbl {{ fill:{GRAY}; font-size:13px; font-weight:600; }}
  text.total {{ fill:#e6edf3; font-size:15px; font-weight:700; }}
  text.sub {{ fill:{GRAY}; font-size:12.5px; font-weight:600; }}
  .c {{ transform-box:fill-box; transform-origin:center; opacity:0; animation:pop {DUR}s ease-out both; }}
  .g {{ animation:pop {DUR}s ease-out both, flash {DUR+0.15}s ease-out both; }}
  @keyframes pop {{ 0%{{opacity:0;transform:scale(.2)}} 60%{{opacity:1;transform:scale(1.1)}} 100%{{opacity:1;transform:scale(1)}} }}
  @keyframes flash {{ 0%{{filter:brightness(2.4)}} 45%{{filter:brightness(2.4)}} 100%{{filter:brightness(1)}} }}
  @media (prefers-reduced-motion: reduce) {{ .c {{ opacity:1 !important; animation:none !important; }} }}
</style>
<rect width="{W}" height="{H}" fill="none"/>
{''.join(labels)}
{''.join(rects)}
<text class="total" x="{LEFT}" y="{H-26}">{total:,} contributions in the last year</text>
<text class="sub" x="{LEFT}" y="{H-8}">current streak <tspan fill="{GREEN}">{cur}</tspan> days&#183;longest <tspan fill="{GREEN}">{longest}</tspan> days{best_txt}</text>
</svg>'''

try:
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(svg)
except OSError as e:
    raise SystemExit(f"[streak] could not write {OUT}: {e}")
print(f"[streak] wrote {OUT}: {n} days, {total:,} contributions, {len(svg)//1024} KB")
