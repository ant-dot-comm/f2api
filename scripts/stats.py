#!/usr/bin/env python3
"""
stats.py — derive per-driver and per-team career/season statistics from the
results + standings, so the frontend can build driver-comparison and team-history
pages without computing anything itself. Writes:

    stats/drivers/<driverId>.json   career + per-season metrics + round-by-round
    stats/teams/<constructorId>.json team history + metrics + drivers
    stats/index.json                driver + team lists (for comparison pickers)

Covers every season present in the repo (expands automatically as historical data
is added). Identical in both repos — it auto-detects the series from the layout.
Run after update_* and standings_* for the season(s).

    python scripts/stats.py            # write all
    python scripts/stats.py --push
"""
import argparse
import glob
import json
import os

import common as C

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Series config (auto-detected). TYPE maps a race-key to its race type; FEATURE
# marks the races whose grid P1 is a championship pole.
if os.path.isdir(os.path.join(REPO, "drivers")):
    SERIES, ROSTER_DIR = "f2", "drivers"
    TYPE = {"race1": "sprint", "race2": "feature", "race3": "feature2"}
    FEATURE = {"race2", "race3"}
else:
    SERIES, ROSTER_DIR = "f1a", "constructors"
    TYPE = {"race0": "opening", "race1": "reverse", "race2": "feature"}
    FEATURE = {"race2"}


def _load(path):
    return json.load(open(path)) if os.path.exists(path) else None


def _seasons():
    return sorted(os.path.basename(os.path.dirname(p))
                  for p in glob.glob(os.path.join(REPO, "races", "*", "resullts.json")))


def _fl_time(row):
    t = row.get("FastestLap", {}).get("Time", {}).get("time", "")
    return C_time(t)


def C_time(s):
    import re
    m = re.match(r"(?:(\d+):)?(\d+):(\d\d\.\d+)$", (s or "").strip())
    if not m:
        return float("inf")
    return int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def _blank():
    return {"entries": 0, "starts": 0, "points": 0,
            "wins": {"total": 0}, "podiums": {"total": 0},
            "poles": 0, "fastestLaps": 0, "dnfs": 0,
            "bestFinish": None, "_fin": []}


def _add_result(s, key, row, got_fl):
    s["entries"] += 1
    status = row["status"]
    if status != "DNS":
        s["starts"] += 1
    if status == "DNF":
        s["dnfs"] += 1
    pos = row["position"]
    if pos.isdigit():
        p = int(pos)
        s["_fin"].append(p)
        s["bestFinish"] = p if s["bestFinish"] is None else min(s["bestFinish"], p)
        t = TYPE.get(key, key)
        if p == 1:
            s["wins"]["total"] += 1
            s["wins"][t] = s["wins"].get(t, 0) + 1
        if p <= 3:
            s["podiums"]["total"] += 1
            s["podiums"][t] = s["podiums"].get(t, 0) + 1
    if key in FEATURE and row.get("grid") == "1":
        s["poles"] += 1
    if got_fl:
        s["fastestLaps"] += 1


def _finalize(s):
    fin = s.pop("_fin")
    s["avgFinish"] = round(sum(fin) / len(fin), 2) if fin else None
    return s


def _merge(into, src):
    for k in ("entries", "starts", "poles", "fastestLaps", "dnfs"):
        into[k] += src[k]
    for grp in ("wins", "podiums"):
        for t, n in src[grp].items():
            into[grp][t] = into[grp].get(t, 0) + n
    into["_fin"].extend(src.get("_fin", []))
    if src.get("bestFinish") is not None:
        into["bestFinish"] = (src["bestFinish"] if into["bestFinish"] is None
                              else min(into["bestFinish"], src["bestFinish"]))


def build():
    seasons = _seasons()
    # name/constructor lookups from each season's registry + roster
    names, dcon = {}, {}      # driverId -> {name,code}; (driverId,season) -> constructorId
    team_names = {}           # constructorId -> display name
    season_complete = {}      # year -> bool (season finished, so a P1 is a real title)
    drv = {}   # driverId -> {"name","code","seasons":{yr:stats},"results":[], "champ":{yr:pos},"points":{yr:n}}
    team = {}  # constructorId -> {"name","seasons":{yr:stats},"champ":{},"points":{},"drivers":set()}

    for yr in seasons:
        res = _load(os.path.join(REPO, "races", yr, "resullts.json")) or []
        reg = _load(os.path.join(REPO, ROSTER_DIR, yr, "registry.json"))
        roster = _load(os.path.join(REPO, ROSTER_DIR, yr, "drivers.json")) or {}
        num2con = {n: i["Constructor"]["constructorId"] for n, i in roster.items()}
        for n, i in roster.items():
            team_names.setdefault(i["Constructor"]["constructorId"], i["Constructor"]["name"])
        if reg:
            for did, r in reg["Drivers"].items():
                nm = (r.get("givenName", "") + " " + r.get("familyName", "")).strip()
                names[did] = {"name": nm or did, "code": r.get("code", "")}
                for e in r["entries"]:
                    for rd in e["rounds"]:
                        dcon[(did, yr, rd)] = e["constructorId"]
        sd = _load(os.path.join(REPO, "standings", yr, "drivers.json"))
        st = _load(os.path.join(REPO, "standings", yr, "teams.json"))
        season_complete[yr] = bool(sd.get("complete")) if sd else False
        champ_d = {e["driverId"]: (e["position"], e["points"]) for e in sd["Standings"]} if sd else {}
        champ_t = {e["constructorId"]: (e["position"], e["points"]) for e in st["Standings"]} if st else {}

        for rnd in res:
            rno = rnd["round"]
            for key, rows in rnd["Results"].items():
                # fastest lap of this race = min valid FL time among classified
                best_t, best_num = float("inf"), None
                for row in rows:
                    if row["position"].isdigit():
                        t = _fl_time(row)
                        if t < best_t:
                            best_t, best_num = t, row["number"]
                for row in rows:
                    did = row.get("driverId") or ""
                    if not did:
                        continue
                    ds = drv.setdefault(did, {"seasons": {}, "results": []})
                    s = ds["seasons"].setdefault(yr, _blank())
                    _add_result(s, key, row, row["number"] == best_num)
                    cid = dcon.get((did, yr, rno)) or num2con.get(row["number"])
                    ds["results"].append({
                        "season": yr, "round": rno, "raceName": rnd["raceName"],
                        "race": key, "type": TYPE.get(key, key), "constructorId": cid,
                        "grid": row.get("grid", ""), "position": row["position"],
                        "status": row["status"], "points": row["points"]})
                    if cid:
                        ts = team.setdefault(cid, {"seasons": {}, "drivers": set()})
                        tss = ts["seasons"].setdefault(yr, _blank())
                        _add_result(tss, key, row, row["number"] == best_num)
                        ts["drivers"].add(did)

        for did, (pos, pts) in champ_d.items():
            drv.setdefault(did, {"seasons": {}, "results": []}).setdefault("champ", {})[yr] = int(pos)
            drv[did].setdefault("points", {})[yr] = int(pts)
        for cid, (pos, pts) in champ_t.items():
            team.setdefault(cid, {"seasons": {}, "drivers": set()}).setdefault("champ", {})[yr] = int(pos)
            team[cid].setdefault("points", {})[yr] = int(pts)

    # write driver files
    os.makedirs(os.path.join(REPO, "stats", "drivers"), exist_ok=True)
    os.makedirs(os.path.join(REPO, "stats", "teams"), exist_ok=True)
    d_index, t_index = [], []
    for did, ds in sorted(drv.items()):
        career = _blank()
        season_out = {}
        for yr, s in sorted(ds["seasons"].items()):
            _merge(career, s)
            champ = ds.get("champ", {}).get(yr)
            pts = ds.get("points", {}).get(yr, 0)
            so = _finalize({**s, "_fin": s["_fin"]})
            so["points"] = pts
            so["championshipPosition"] = champ
            so["complete"] = season_complete.get(yr, False)
            season_out[yr] = so
        career = _finalize(career)
        career["points"] = sum(ds.get("points", {}).values())
        champs = [y for y, p in ds.get("champ", {}).items()
                  if p == 1 and season_complete.get(y)]
        career["titles"] = len(champs)
        allpos = [p for p in ds.get("champ", {}).values()]
        career["bestChampionship"] = min(allpos) if allpos else None
        meta = names.get(did, {"name": did, "code": ""})
        doc = {"driverId": did, "name": meta["name"], "code": meta["code"],
               "series": SERIES, "seasons": sorted(ds["seasons"]),
               "career": career, "bySeasons": season_out, "results": ds["results"]}
        C.write_json(os.path.join(REPO, "stats", "drivers", f"{did}.json"), doc)
        d_index.append({"driverId": did, "name": meta["name"], "code": meta["code"],
                        "seasons": sorted(ds["seasons"]), "points": career["points"]})

    for cid, ts in sorted(team.items()):
        career = _blank()
        season_out = {}
        for yr, s in sorted(ts["seasons"].items()):
            _merge(career, s)
            so = _finalize({**s, "_fin": s["_fin"]})
            so["points"] = ts.get("points", {}).get(yr, 0)
            so["championshipPosition"] = ts.get("champ", {}).get(yr)
            so["complete"] = season_complete.get(yr, False)
            season_out[yr] = so
        career = _finalize(career)
        career["points"] = sum(ts.get("points", {}).values())
        career["titles"] = len([1 for y, p in ts.get("champ", {}).items()
                                if p == 1 and season_complete.get(y)])
        allpos = list(ts.get("champ", {}).values())
        career["bestChampionship"] = min(allpos) if allpos else None
        doc = {"constructorId": cid, "name": team_names.get(cid, cid), "series": SERIES,
               "seasons": sorted(ts["seasons"]), "career": career, "bySeasons": season_out,
               "drivers": sorted(ts["drivers"])}
        C.write_json(os.path.join(REPO, "stats", "teams", f"{cid}.json"), doc)
        t_index.append({"constructorId": cid, "name": team_names.get(cid, cid),
                        "seasons": sorted(ts["seasons"]), "points": career["points"]})

    d_index.sort(key=lambda x: -x["points"])
    t_index.sort(key=lambda x: -x["points"])
    C.write_json(os.path.join(REPO, "stats", "index.json"),
                 {"series": SERIES, "updated": C.today_iso(),
                  "drivers": d_index, "teams": t_index})
    return len(d_index), len(t_index), seasons


def main():
    ap = argparse.ArgumentParser(description="Build derived driver/team stats")
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()
    nd, nt, seasons = build()
    print(f"[{SERIES}] wrote stats for {nd} drivers, {nt} teams across seasons {seasons}")
    if a.push:
        import subprocess
        subprocess.run(["git", "-C", REPO, "add", "stats"], check=True)
        subprocess.run(["git", "-C", REPO, "commit", "-m", f"Build {SERIES} stats layer"], check=True)
        subprocess.run(["git", "-C", REPO, "push"], check=True)


if __name__ == "__main__":
    main()
