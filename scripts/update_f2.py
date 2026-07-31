#!/usr/bin/env python3
"""
update_f2.py — pull F2 race results straight from the FIA timing PDFs and write
them into races/<year>/resullts.json in this repo's schema. No screenshots.

The FIA publishes text-based PDFs (Al Kamel timing) for every session. This
script downloads the two "final classification" PDFs (Race 1 sprint, Race 2
feature) plus the two "final grid" PDFs, parses them, and upserts the round.

Typical use, the weekend a round finishes (its docs are the latest on the FIA
season page, which is the only event that page serves without JavaScript):

    python scripts/update_f2.py --event Budapest
    python scripts/update_f2.py --event Budapest --push      # also git commit+push

Backfilling an older round (not the latest on the FIA page): pass the four PDF
URLs explicitly — grab them from the event's page on fia.com:

    python scripts/update_f2.py --event Monaco \
        --race1-cls URL --race1-grid URL --race2-cls URL --race2-grid URL

Run `pip install -r scripts/requirements.txt` once first.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request

import pdfplumber

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASON_PAGE = ("https://www.fia.com/documents/championships/"
               "formula-2-championship-44/season/season-2026-2072")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"

# FIA event name -> round metadata for this repo. Add rows as the calendar grows.
EVENTS = {
    "Melbourne":            (1, "Australian Grand Prix",         "albert_park",   "Albert Park Grand Prix Circuit"),
    "Miami":                (2, "Miami Grand Prix",              "miami",         "Miami International Circuit"),
    "Montréal":             (3, "Canadian Grand Prix",           "villeneuve",    "Circuit Gilles Villeneuve"),
    "Monaco":               (4, "Monaco Grand Prix",             "monaco",        "Circuit de Monaco"),
    "Barcelona-Catalunya":  (5, "Spanish Grand Prix",            "catalunya",     "Circuit de Barcelona-Catalunya"),
    "Spielberg":            (6, "Austrian Grand Prix",           "red_bull_ring", "Red Bull Ring"),
    "Silverstone":          (7, "British Grand Prix",            "silverstone",   "Silverstone Circuit"),
    "Spa-Francorchamps":    (8, "Belgian Grand Prix",            "spa",           "Circuit de Spa-Francorchamps"),
    "Budapest":             (9, "Hungarian Grand Prix",          "hungaroring",   "Hungaroring"),
}

LAP = re.compile(r'^(?:\d+:)?\d{1,2}:\d\d\.\d{3}$')   # mm:ss.xxx or h:mm:ss.xxx
INT = re.compile(r'^\d+$')


# --------------------------------------------------------------------------- #
# fetching
# --------------------------------------------------------------------------- #
def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=60).read()


def download_pdf(url):
    fd, path = tempfile.mkstemp(suffix=".pdf")
    with os.fdopen(fd, "wb") as f:
        f.write(fetch(url))
    return path


def discover_links(event):
    """Find the 4 PDF urls for `event` from the FIA season page (latest event only)."""
    html = fetch(SEASON_PAGE).decode("utf-8", "replace")
    ev = event.lower().replace("-", "[-_ ]").replace("é", ".")
    hrefs = re.findall(r'href="(/system/files/[^"]+\.pdf)"', html, re.I)
    hrefs = ["https://www.fia.com" + h for h in hrefs if re.search(ev, h, re.I)]

    def pick(race_kw, kind):
        # kind: 'classification' or 'grid' (grid matches final_grid OR final_starting_grid)
        for h in hrefs:
            low = h.lower()
            if race_kw in low and kind in low:
                return h
        return None

    return {
        "race1_cls":  pick("race_1", "classification"),
        "race1_grid": pick("race_1", "grid"),
        "race2_cls":  pick("race_2", "classification"),
        "race2_grid": pick("race_2", "grid"),
    }


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #
def _pdf_text(path):
    with pdfplumber.open(path) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


def parse_classification(path):
    """Rows in repo schema (grid filled in later). Handles Finished / DNF / DNS."""
    rows, section = [], "classified"
    for raw in _pdf_text(path).splitlines():
        line = raw.strip()
        if not line:
            continue
        up = line.upper()
        if up.startswith("NOT CLASSIFIED"):
            section = "not_classified"
            continue
        if up.startswith("NO DRIVER"):        # table header row
            continue
        # Trailer blocks that come *after* the classification table. (Cover-page
        # lines like "The Stewards" are ignored naturally — they don't start
        # with an integer — so they must NOT appear here.)
        if up.startswith(("OVERALL FASTEST", "FASTEST LAP", "* PENALTIES", "TIMEKEEPER")):
            break
        t = line.split()
        if len(t) < 4 or not INT.match(t[0]):
            continue
        laps_t = [i for i, x in enumerate(t) if LAP.match(x)]

        if section == "classified":
            # POS NUM name.. team.. LAPS TIME [GAP] [INT] KMH FASTEST ON [PTS]
            if len(laps_t) < 2:
                continue
            time_i, fast_i = laps_t[0], laps_t[-1]
            on = t[fast_i + 1] if fast_i + 1 < len(t) and INT.match(t[fast_i + 1]) else "0"
            mid = t[time_i + 1:fast_i]                 # [gap?, int?, kmh]
            rows.append({
                "number": t[1], "grid": "", "position": t[0], "laps": t[time_i - 1],
                "gap": mid[0] if len(mid) >= 2 else "-", "status": "Finished",
                "Time": {"time": t[time_i]},
                "FastestLap": {"lap": on, "Time": {"time": t[fast_i]}},
            })
        else:
            num = t[0]
            up_tokens = {x.upper() for x in t}
            if "DNS" in up_tokens or "DNW" in up_tokens:
                status = "DNS" if "DNS" in up_tokens else "DNW"
                ints = [x for x in t[1:] if INT.match(x)]
                rows.append({
                    "number": num, "grid": "", "position": status,
                    "laps": ints[-1] if ints else "0", "gap": status, "status": status,
                    "Time": {"time": status}, "FastestLap": {"lap": "0", "Time": {"time": "-"}},
                })
                continue
            # DNF: NUM name.. team.. LAPS TIME DNF KMH FASTEST ON
            if laps_t:
                time_i = laps_t[0]
                laps = t[time_i - 1] if INT.match(t[time_i - 1]) else "0"
                race_time = t[time_i]
                if len(laps_t) >= 2:
                    on = t[laps_t[-1] + 1] if laps_t[-1] + 1 < len(t) and INT.match(t[laps_t[-1] + 1]) else "0"
                    fastest = t[laps_t[-1]]
                else:
                    fastest, on = "-", "0"
            else:
                ints = [x for x in t[1:] if INT.match(x)]
                laps = ints[-1] if ints else "0"
                race_time, fastest, on = "DNF", "-", "0"
            rows.append({
                "number": num, "grid": "", "position": "NC", "laps": laps,
                "gap": "DNF", "status": "DNF", "Time": {"time": race_time},
                "FastestLap": {"lap": on, "Time": {"time": fastest}},
            })
    return rows


def parse_grid(path):
    """{carNumber: gridPos}. Grid PDFs list 'POS NUM Name [laptime]' sequentially
    1..N (two columns). Anchor on 'POS NUM Capitalized-name' so cars with no lap
    time (pit-lane / permitted-to-start) are still captured; enforce increasing
    position order to reject stray matches in team names / penalty notes."""
    text = _pdf_text(path)
    cut = re.search(r'\*?\s*PENALTIES', text)
    if cut:
        text = text[:cut.start()]
    grid, last = {}, 0
    for m in re.finditer(r"(?<!\d)(\d{1,2})\s+(\d{1,2})\s+[A-Z][A-Za-z.\-']", text):
        pos, num = int(m.group(1)), m.group(2)
        if last < pos <= 24 and num not in grid:
            grid[num] = str(pos)
            last = pos
    return grid


def build_race(links, meta):
    round_no, race_name, circuit_id, circuit_name = meta
    out = {"season": "2026", "round": str(round_no), "raceName": race_name,
           "Circuit": {"circuitId": circuit_id, "circuitName": circuit_name},
           "Results": {}}
    for key, cls_url, grid_url in [("race1", links["race1_cls"], links["race1_grid"]),
                                   ("race2", links["race2_cls"], links["race2_grid"])]:
        if not cls_url:
            print(f"  ! no classification PDF for {key}; skipping", file=sys.stderr)
            continue
        cls_path = download_pdf(cls_url)
        rows = parse_classification(cls_path)
        os.unlink(cls_path)
        if grid_url:
            grid_path = download_pdf(grid_url)
            g = parse_grid(grid_path)
            os.unlink(grid_path)
            for r in rows:
                r["grid"] = g.get(r["number"], r["grid"])
        out["Results"][key] = rows
        print(f"  {key}: {len(rows)} rows"
              f"{' (no grid PDF)' if not grid_url else ''}")
    return out


# --------------------------------------------------------------------------- #
# writing + git
# --------------------------------------------------------------------------- #
def upsert(race):
    path = os.path.join(REPO, "races", "2026", "resullts.json")
    data = json.load(open(path))
    data = [r for r in data if r["round"] != race["round"]] + [race]
    data.sort(key=lambda r: int(r["round"]))
    with open(path, "w") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
        f.write("\n")
    return path


def git_push(path, race):
    msg = f"F2 {race['raceName']} (round {race['round']}) results"
    for cmd in (["git", "add", path], ["git", "commit", "-m", msg], ["git", "push"]):
        subprocess.run(cmd, cwd=REPO, check=True)


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Update F2 results from FIA timing PDFs.")
    ap.add_argument("--event", required=True, help="FIA event name, e.g. Budapest (see EVENTS map)")
    ap.add_argument("--race1-cls"); ap.add_argument("--race1-grid")
    ap.add_argument("--race2-cls"); ap.add_argument("--race2-grid")
    ap.add_argument("--push", action="store_true", help="git add/commit/push after writing")
    a = ap.parse_args()

    if a.event not in EVENTS:
        sys.exit(f"Unknown event '{a.event}'. Known: {', '.join(EVENTS)}")
    meta = EVENTS[a.event]

    if a.race1_cls or a.race2_cls:            # explicit URLs (backfill)
        links = {"race1_cls": a.race1_cls, "race1_grid": a.race1_grid,
                 "race2_cls": a.race2_cls, "race2_grid": a.race2_grid}
    else:                                     # auto-discover latest event
        print(f"Discovering PDFs for {a.event} on fia.com …")
        links = discover_links(a.event)
        missing = [k for k, v in links.items() if not v]
        if missing:
            print("  ! could not find:", ", ".join(missing),
                  "\n    (older events aren't served without JS — pass --raceN-cls/--raceN-grid URLs)",
                  file=sys.stderr)

    race = build_race(links, meta)
    if not race["Results"]:
        sys.exit("No results parsed; aborting.")
    path = upsert(race)
    print(f"Wrote round {race['round']} → {os.path.relpath(path, REPO)}")
    if a.push:
        git_push(path, race)
        print("Committed and pushed.")


if __name__ == "__main__":
    main()
