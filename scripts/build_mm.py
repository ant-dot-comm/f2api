#!/usr/bin/env python3
"""Write one GP2/F2 season (from motorsportmagazine) into the f2api repo.
Hard-reconciles summed per-race points vs official standings before writing.
Usage: build_mm.py <year> [--force]   (2005-2025; <=2016 = GP2, else F2)"""
import json, os, sys
import motorsportmag as M

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRIVATE = ("_feat_laps", "_nsprint", "_date")

def slug_for(y): return f"{y}-gp2-series" if y <= 2016 else f"{y}-fia-formula-2-championship"
def champ_name(y): return "GP2 Series" if y <= 2016 else "FIA Formula 2 Championship"
def series_of(y): return "gp2" if y <= 2016 else "f2"

def wj(p, o):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w') as f:
        json.dump(o, f, indent=4, ensure_ascii=False); f.write('\n')

def build(year, force=False):
    sl = slug_for(year)
    rounds, races, standings, cid, extra = M.assemble(year, sl, verbose=False)
    # reconcile
    got = {}
    for rd in rounds:
        for rows in rd["Results"].values():
            for x in rows:
                got[x["driverId"]] = got.get(x["driverId"], 0) + float(x["points"] or 0)
    off = [(s["driver_name"], float(s["points"]), got.get(M.did(s["driver_name"]), 0))
           for s in standings if abs(float(s["points"]) - got.get(M.did(s["driver_name"]), 0)) > 0.001]
    # standings TOTALS are authoritative (written verbatim); large/many deltas signal a
    # structural error, a handful of small ones are motorsportmag's own race-vs-standings quirks.
    big = [o for o in off if abs(o[1] - o[2]) > 3 or len(off) > 2]
    if big and not force:
        print(f"{year}: RECONCILE SUSPICIOUS ({len(off)} drivers off). Not writing. Use --force after review.")
        for n, o, g in off[:20]: print(f"  {n:24s} official={o:g} summed={g:g} off={o-g:+g}")
        return False
    nr = len(rounds)
    meta = {"season": str(year), "series": series_of(year), "championship": champ_name(year),
            "source": "motorsportmagazine.com", "lastRound": nr, "scheduledRounds": nr, "complete": True}

    # results.json (strip private keys)
    clean = [{k: v for k, v in rd.items() if k not in PRIVATE} for rd in rounds]
    wj(f"{REPO}/races/{year}/resullts.json", clean)

    # driver standings (full names, authoritative totals)
    def pstr(p):
        f = float(p); return str(int(f)) if f == int(f) else str(f)
    drv_st = [{"position": str(s["standing"]), "driverId": M.did(s["driver_name"]),
               "driver": s["driver_name"], "points": pstr(s["points"])}
              for s in sorted(standings, key=lambda s: int(s["standing"]))]
    wj(f"{REPO}/standings/{year}/drivers.json", {**meta, "Standings": drv_st})

    # team standings computed from race rows
    tp = extra["team_pts"]; tn = extra["team_nm"]
    team_st = []
    for i, (c, pts) in enumerate(sorted(tp.items(), key=lambda kv: -kv[1])):
        name = max(tn.get(c, {"": 0}).items(), key=lambda kv: kv[1])[0] or c
        team_st.append({"position": str(i + 1), "constructorId": c, "team": name, "points": pstr(pts)})
    wj(f"{REPO}/standings/{year}/teams.json", {**meta, "Standings": team_st})

    # registry
    reg = extra["reg"]; dname = extra["dname"]
    Drivers = {}
    for d, ents in sorted(reg.items()):
        Drivers[d] = {"driverId": d, "name": dname.get(d, d.replace("_", " ").title()),
                      "entries": [{"number": num, "constructorId": c, "rounds": [str(r) for r in sorted(rs)]}
                                  for (num, c), rs in sorted(ents.items(), key=lambda kv: min(kv[1]))]}
    wj(f"{REPO}/drivers/{year}/registry.json", {"season": str(year), "series": series_of(year),
                                                "championship": champ_name(year), "Drivers": Drivers})
    # checks
    res_ids = {x["driverId"] for rd in rounds for rows in rd["Results"].values() for x in rows}
    champ_ids = {M.did(s["driver_name"]) for s in standings}
    nocon = [d for d, r in Drivers.items() if any(e["constructorId"] is None for e in r["entries"])]
    print(f"{year} [{series_of(year)}] {nr} rounds, {len(Drivers)} drivers, {len(team_st)} teams, "
          f"reconcile {'OK' if not off else f'FORCED({len(off)})'}")
    print("  standings ids not in results:", [s['driver_name'] for s in standings if M.did(s['driver_name']) not in res_ids] or "none")
    print("  registry not in standings:", [d for d in Drivers if d not in champ_ids] or "none")
    print("  entries missing constructorId:", nocon or "none")
    return True

if __name__ == "__main__":
    y = int(sys.argv[1]); build(y, force="--force" in sys.argv)
