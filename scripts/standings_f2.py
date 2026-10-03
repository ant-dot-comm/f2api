#!/usr/bin/env python3
"""
standings_f2.py — pull the official F2 championship standings (driver + team)
from fiaformula2.com and write them into standings/<year>/{drivers,teams}.json.

fiaformula2.com server-renders the standings as a real HTML table (points already
include pole + fastest-lap bonuses), with a per-round SR/FR breakdown. We mirror
that as the published numbers — the source of truth — and attach our own driverId /
constructorId. Races per round are auto-detected from the SR/FR column labels, so
3-race weekends (e.g. Baku 2026 = sprint + 2 features) are handled automatically.

NOTE: fiaformula2.com serves standings for the CURRENT season only, so run this
during the live season to snapshot it into the API. Historical seasons are built
by computing from the FIA per-race PTS columns instead (see the backfill tooling).

    python scripts/standings_f2.py --season 2026
    python scripts/standings_f2.py --season 2026 --push
"""
import argparse
import os
import sys

import common as C

STANDINGS_URL = "https://www.fiaformula2.com/en/standings/{year}/{kind}"


def round_groups(table):
    """Group the SR/FR subheader labels into rounds (a new round starts at each
    'SR'); returns e.g. [['SR','FR'], ..., ['SR','FR','FR'], ...] in calendar order."""
    labels = [C.cell_text(th) for th in table.xpath(".//thead//th")
              if th.get("colspan") in (None, "1")]
    labels = [l for l in labels if l in ("SR", "FR")]
    groups, cur = [], None
    for lab in labels:
        if lab == "SR":
            cur = [lab]
            groups.append(cur)
        elif cur is not None:
            cur.append(lab)
    return groups


def session_keys(group):
    """SR -> sprint, 1st FR -> feature, 2nd FR -> feature2."""
    keys, nf = [], 0
    for lab in group:
        if lab == "SR":
            keys.append("sprint")
        else:
            keys.append("feature" if nf == 0 else "feature2")
            nf += 1
    return keys


def by_round(values, groups):
    """Map a row's per-cell values onto rounds; skip all-'-' (future) rounds."""
    out, i = [], 0
    for rn, grp in enumerate(groups, start=1):
        vs = values[i:i + len(grp)]
        i += len(grp)
        if all(v in ("-", "") for v in vs):
            continue
        rec = {"round": str(rn)}
        for k, v in zip(session_keys(grp), vs):
            rec[k] = v
        out.append(rec)
    return out


def parse_standings(url, resolve, name_field):
    """Return (standings list, unmatched names). `resolve(name)->id` maps the row
    label to a driverId/constructorId."""
    import re
    table = C.fetch_html(url).xpath("//table")[0]
    groups = round_groups(table)
    out, unmatched = [], []
    for cells in C.table_rows(table):
        head, values, total = cells[0], cells[1:-1], cells[-1]
        m = re.match(r"(\d+)\s*(.*)", head)
        pos, name = m.group(1), m.group(2).strip()
        _id = resolve(name)
        if _id is None:
            unmatched.append(name)
        entry = {"position": pos, f"{name_field}Id": _id, name_field: name,
                 "points": total, "byRound": by_round(values, groups)}
        out.append(entry)
    return out, unmatched


def build(season):
    import json
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    roster = json.load(open(os.path.join(repo, "drivers", str(season), "drivers.json")))
    surname2id, team2id = {}, {}
    for info in roster.values():
        d = info["Driver"]
        surname2id[C.deaccent(d["familyName"]).upper()] = d["driverId"]
        c = info["Constructor"]
        team2id[c["name"].upper()] = c["constructorId"]

    def resolve_driver(name):
        return surname2id.get(C.deaccent(C.surname_of(name)).upper())

    def resolve_team(name):
        return team2id.get(name.upper())

    drivers, d_unmatched = parse_standings(
        STANDINGS_URL.format(year=season, kind="drivers"), resolve_driver, "driver")
    teams, t_unmatched = parse_standings(
        STANDINGS_URL.format(year=season, kind="teams"), resolve_team, "team")

    # Fallback ids for anyone not in the roster (replacement drivers / renamed teams)
    for e in drivers:
        if e["driverId"] is None:
            e["driverId"] = C.slug(C.surname_of(e["driver"]))
    for e in teams:
        if e["teamId"] is None:
            e["teamId"] = C.slug(e["team"])

    last_round = max((int(r["round"]) for e in drivers for r in e["byRound"]), default=0)
    meta = {"season": str(season), "series": "f2", "updated": C.today_iso(),
            "lastRound": last_round}
    d_doc = {**meta, "source": STANDINGS_URL.format(year=season, kind="drivers"),
             "Standings": drivers}
    t_doc = {**meta, "source": STANDINGS_URL.format(year=season, kind="teams"),
             "Standings": teams}
    d_path = C.write_json(os.path.join(repo, "standings", str(season), "drivers.json"), d_doc)
    t_path = C.write_json(os.path.join(repo, "standings", str(season), "teams.json"), t_doc)
    return d_path, t_path, d_unmatched, t_unmatched, last_round


def main():
    ap = argparse.ArgumentParser(description="Update F2 standings from fiaformula2.com")
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()
    dp, tp, du, tu, lr = build(a.season)
    print(f"Wrote {dp} ({lr} rounds)")
    print(f"Wrote {tp}")
    if du:
        print(f"  ⚠ drivers not in roster (need registry entries): {du}")
    if tu:
        print(f"  ⚠ teams not in roster: {tu}")
    if a.push:
        import subprocess
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        subprocess.run(["git", "-C", repo, "add", "standings"], check=True)
        subprocess.run(["git", "-C", repo, "commit", "-m",
                        f"Add F2 {a.season} standings (driver + team)"], check=True)
        subprocess.run(["git", "-C", repo, "push"], check=True)


if __name__ == "__main__":
    main()
