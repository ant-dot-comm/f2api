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

import common as C

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS_WORDS = {"DNF", "DNS", "DNW", "NC", "DSQ", "EX", "DNQ"}
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
    "Monza":                (10, "Italian Grand Prix",           "monza",         "Autodromo Nazionale di Monza"),
    "Madrid":               (11, "Spanish Grand Prix",           "madring",       "Madring"),
    "Baku":                 (12, "Azerbaijan Grand Prix",        "baku",          "Baku City Circuit"),
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
        "race3_cls":  pick("race_3", "classification"),
        "race3_grid": pick("race_3", "grid"),
    }


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #
def _pdf_text(path):
    with pdfplumber.open(path) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


def _name_tokens(tokens):
    """Alpha tokens of a classification row = given + surname + team (resolved later)."""
    return [x for x in tokens if any(c.isalpha() for c in x)
            and x.upper() not in STATUS_WORDS and not LAP.match(x)]


def parse_classification(path):
    """Rows in repo schema (grid + driverId filled in later). Handles Finished /
    DNF / DNS, captures the PTS column and the driver-name tokens (`_name`)."""
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
        name = _name_tokens(t)

        if section == "classified":
            # POS NUM name.. team.. LAPS TIME [GAP] [INT] KMH FASTEST ON [PTS]
            if len(laps_t) < 2:
                continue
            time_i, fast_i = laps_t[0], laps_t[-1]
            on = t[fast_i + 1] if fast_i + 1 < len(t) and INT.match(t[fast_i + 1]) else "0"
            pts = t[fast_i + 2] if fast_i + 2 < len(t) and INT.match(t[fast_i + 2]) else "0"
            mid = t[time_i + 1:fast_i]                 # [gap?, int?, kmh]
            rows.append({
                "number": t[1], "grid": "", "position": t[0], "laps": t[time_i - 1],
                "gap": mid[0] if len(mid) >= 2 else "-", "status": "Finished",
                "points": pts, "_name": name,
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
                    "points": "0", "_name": name,
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
                "gap": "DNF", "status": "DNF", "points": "0", "_name": name,
                "Time": {"time": race_time},
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


# race key -> (classification link key, grid link key). race3 = 2nd feature on the
# rare 3-race weekend (e.g. Baku 2026); race0 reserved for an opening race.
RACE_KEYS = [("race1", "race1_cls", "race1_grid"),
             ("race2", "race2_cls", "race2_grid"),
             ("race3", "race3_cls", "race3_grid")]

ROW_ORDER = ("number", "driverId", "grid", "position", "laps", "gap",
             "status", "points", "Time", "FastestLap")


def _load_roster(season):
    """Return (roster-by-number, [(SURNAME, driverId)] longest-first)."""
    roster = json.load(open(os.path.join(REPO, "drivers", str(season), "drivers.json")))
    surnames = [(C.deaccent(i["Driver"]["familyName"]).upper(), i["Driver"]["driverId"])
                for i in roster.values()]
    surnames.sort(key=lambda x: -len(x[0]))
    return roster, surnames


def resolve_driver(num, name_toks, roster, surnames):
    """(driverId, is_replacement). Match the row's name against roster surnames;
    a name not in the roster is a mid-season replacement -> slug of its surname."""
    joined = " ".join(C.deaccent(x).upper() for x in name_toks)
    for fam_up, sid in surnames:
        if re.search(r"\b" + re.escape(fam_up) + r"\b", joined):
            return sid, False
    entry = roster.get(str(num))
    span = list(name_toks)
    if entry:                                   # strip the entry's team from the tail
        tt = C.deaccent(entry["Constructor"]["name"]).upper().split()
        while tt and span and C.deaccent(span[-1]).upper() == tt[-1]:
            span.pop(); tt.pop()
    surname = " ".join(span[1:]) if len(span) > 1 else (span[0] if span else "")
    return (C.slug(surname) if surname else None), True


def build_race(links, meta, season=2026):
    round_no, race_name, circuit_id, circuit_name = meta
    roster, surnames = _load_roster(season)
    out = {"season": str(season), "round": str(round_no), "raceName": race_name,
           "Circuit": {"circuitId": circuit_id, "circuitName": circuit_name},
           "Results": {}}
    replacements = []
    for key, cls_k, grid_k in RACE_KEYS:
        cls_url, grid_url = links.get(cls_k), links.get(grid_k)
        if not cls_url:
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
        for r in rows:
            sid, is_repl = resolve_driver(r["number"], r.pop("_name", []), roster, surnames)
            r["driverId"] = sid or ""
            if is_repl:
                replacements.append(f"{key} #{r['number']}->{sid}")
        out["Results"][key] = [{k: r[k] for k in ROW_ORDER} for r in rows]
        print(f"  {key}: {len(rows)} rows{' (no grid PDF)' if not grid_url else ''}")
    if replacements:
        print(f"  ⚠ replacement driver(s): {', '.join(replacements)}")
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
    ap.add_argument("--race3-cls"); ap.add_argument("--race3-grid")  # rare 3-race weekend
    ap.add_argument("--push", action="store_true", help="git add/commit/push after writing")
    a = ap.parse_args()

    if a.event not in EVENTS:
        sys.exit(f"Unknown event '{a.event}'. Known: {', '.join(EVENTS)}")
    meta = EVENTS[a.event]

    if a.race1_cls or a.race2_cls:            # explicit URLs (backfill)
        links = {"race1_cls": a.race1_cls, "race1_grid": a.race1_grid,
                 "race2_cls": a.race2_cls, "race2_grid": a.race2_grid,
                 "race3_cls": a.race3_cls, "race3_grid": a.race3_grid}
    else:                                     # auto-discover latest event
        print(f"Discovering PDFs for {a.event} on fia.com …")
        links = discover_links(a.event)
        missing = [k for k, v in links.items() if not v and not k.startswith("race3")]
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
