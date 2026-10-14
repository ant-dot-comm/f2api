#!/usr/bin/env python3
"""Scrape GP2 (2005-16) + F2 (2017-25) from motorsportmagazine.com's WordPress
JSON API into the f2api schema. Points are AUTHORITATIVE per race (incl. pole/FL
bonus), full driver names, grids via position+movement, FL via the race data-box.
Caches every HTTP response in scratchpad/mm_cache so re-runs are instant + polite.

CLI:
  motorsportmag.py teams <year..>     dump distinct team_url slugs -> con()
  motorsportmag.py probe <year>       assemble + reconcile, print diagnostics
"""
import urllib.request, json, re, os, time, unicodedata, sys
from datetime import datetime

import tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.environ.get("MM_CACHE") or os.path.join(tempfile.gettempdir(), "mm_cache")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
AJAX = "https://www.motorsportmagazine.com/database/wp-admin/admin-ajax.php"

def _get(url):
    os.makedirs(CACHE, exist_ok=True)
    key = re.sub(r'[^A-Za-z0-9]+', '_', url)[-190:]
    p = os.path.join(CACHE, key)
    if os.path.exists(p):
        return open(p, 'rb').read()
    for att in range(4):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60).read()
            break
        except Exception:
            if att == 3: raise
            time.sleep(2 + att)
    open(p, 'wb').write(r); time.sleep(0.25)
    return r

def page(url): return _get(url).decode('utf-8', 'replace')
def post_id(url):
    m = re.search(r'"post_id":(\d+)', page(url)); return m.group(1) if m else None
def ajax(action, **params):
    q = "".join(f"&{k}={v}" for k, v in params.items())
    return json.loads(_get(f"{AJAX}?action={action}{q}"))["data"]

def slug(s):
    s = ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn').lower()
    return re.sub(r'[^a-z0-9]+', '_', s).strip('_')

# Surnames shared by two DIFFERENT drivers across 2005-2026 -> disambiguate by first name.
# Kush Maini keeps the plain "maini" id (he is in the live 2026 season, not rebuilt).
COLLIDE_SURNAMES = {"leclerc", "maini", "negrao", "pic", "piquet", "sato"}
KEEP_SURNAME = {"maini": "kush"}

SUFFIX = {"jr", "jr.", "jnr", "sr", "sr.", "ii", "iii", "iv"}
def _surname_word(p):
    i = len(p) - 1
    while i > 0 and slug(p[i]) in SUFFIX:   # skip trailing Jr/II/... suffixes
        i -= 1
    return p[i]

def did(full_name):
    """driverId = slug(surname); first-name-qualified when the surname collides."""
    p = full_name.split()
    if not p: return slug(full_name)
    sur = slug(_surname_word(p)); first = slug(p[0])
    if sur in COLLIDE_SURNAMES and KEEP_SURNAME.get(sur) != first:
        return f"{first}_{sur}"
    return sur

def surname_slug(full_name):
    p = full_name.split(); return slug(_surname_word(p)) if p else slug(full_name)

def team_slug(team_url):
    m = re.search(r'/teams/([^/]+)/', team_url or ''); return m.group(1) if m else ''

# team_url slug -> constructorId (continuity with existing repo ids where a team persists)
CON = {
 "prema-powerteam": "prema", "prema-racing": "prema",
 "art-grand-prix": "art", "dams": "dams", "dams-lucas-oil": "dams",
 "campos-racing": "campos", "campos-grand-prix": "campos", "addax-team": "campos", "barwa-addax-team": "campos",
 "hitech-grand-prix": "hitech", "hitech-pulse-eight": "hitech", "hitech-gp": "hitech",
 "trident": "trident", "trident-racing": "trident", "mp-motorsport": "mp",
 "carlin": "carlin", "carlin-motorsport": "carlin", "rodin-carlin": "rodin", "rodin-motorsport": "rodin",
 "virtuosi-racing": "virtuosi", "uni-virtuosi-racing": "virtuosi", "uni-virtuosi": "virtuosi",
 "russian-time": "russian_time", "invicta-racing": "invicta",
 "van-amersfoort-racing": "var", "charouz-racing-system": "charouz", "hwa-racelab": "hwa",
 "aix-racing": "aix", "phm-racing": "phm", "racing-engineering": "racing_engineering",
}
def con(team_url):
    s = team_slug(team_url)
    return CON.get(s, slug(s)) if s else None

def champ_id(slug_name): return post_id(f"https://www.motorsportmagazine.com/database/championships/{slug_name}/")
def season_races(cid): return ajax("fetch_database_championship_races", championship_id=cid)
def season_standings(cid): return ajax("fetch_database_championship_standings", championship_id=cid)
def race_results(rid, sprint=0): return ajax("fetch_database_race_results", race_id=rid, sprint=sprint, page=1, perPage=100)
def race_quali(rid): return ajax("fetch_database_race_qualifying", race_id=rid, sprint=0, page=1, perPage=100)
def race_box(rid): return ajax("fetch_database_race_data_box", race_id=rid)

def pdate(s):
    try: return datetime.strptime(s.strip(), "%d/%m/%Y")
    except Exception: return None

# ---- time / gap helpers -------------------------------------------------
def t2s(x):
    """'58m 51.044s' or '1h 02m 03.4s' or '1m 27.807s' -> seconds, else None."""
    if not x: return None
    x = x.strip()
    h = re.search(r'(\d+)\s*h', x); m = re.search(r'(\d+)\s*m', x); s = re.search(r'([\d.]+)\s*s', x)
    if not s and not m: return None
    return (int(h.group(1)) * 3600 if h else 0) + (int(m.group(1)) * 60 if m else 0) + (float(s.group(1)) if s else 0)

def fmt_gap(sec):
    if sec is None: return ""
    return f"+{sec:.3f}"

def fmt_lap(raw):
    """'1m 34.050s' / '1h 02m 03.4s' -> 'M:SS.mmm' / 'H:MM:SS.mmm' (stats-parseable)."""
    sec = t2s(raw)
    if sec is None: return ""
    h = int(sec // 3600); m = int((sec % 3600) // 60); s = sec % 60
    return (f"{h}:{m:02d}:{s:06.3f}" if h else f"{m}:{s:06.3f}")

# motorsportmag circuit slug -> (circuitId, raceName) aligned to the existing repo convention
CIRC = {
 "albert-park": ("albert_park", "Australian Grand Prix"),
 "baku": ("baku", "Azerbaijan Grand Prix"),
 "catalunya": ("catalunya", "Spanish Grand Prix"),
 "hockenheim": ("hockenheim", "German Grand Prix"),
 "hungaroring": ("hungaroring", "Hungarian Grand Prix"),
 "imola": ("imola", "Emilia Romagna Grand Prix"),
 "istanbul-park": ("istanbul", "Turkish Grand Prix"),
 "jeddah": ("jeddah", "Saudi Arabian Grand Prix"),
 "jerez": ("jerez", "European Grand Prix"),
 "losail": ("losail", "Qatar Grand Prix"),
 "magny-cours": ("magny_cours", "French Grand Prix"),
 "marina-bay": ("marina_bay", "Singapore Grand Prix"),
 "monte-carlo": ("monaco", "Monaco Grand Prix"),
 "monza": ("monza", "Italian Grand Prix"),
 "mugello": ("mugello", "Tuscan Grand Prix"),
 "nurburgring": ("nurburgring", "European Grand Prix"),
 "paul-ricard": ("ricard", "French Grand Prix"),
 "portimao": ("portimao", "Portuguese Grand Prix"),
 "sakhir": ("bahrain", "Bahrain Grand Prix"),
 "sepang": ("sepang", "Malaysian Grand Prix"),
 "silverstone": ("silverstone", "British Grand Prix"),
 "sochi": ("sochi", "Russian Grand Prix"),
 "spa-francorchamps": ("spa", "Belgian Grand Prix"),
 "spielberg": ("red_bull_ring", "Austrian Grand Prix"),
 "valencia": ("valencia", "European Grand Prix"),
 "valencia-street-circuit": ("valencia", "European Grand Prix"),
 "yas-marina": ("yas_marina", "Abu Dhabi Grand Prix"),
 "zandvoort": ("zandvoort", "Dutch Grand Prix"),
}
def circuit_of(url, name):
    m = re.search(r'/circuits/([^/]+)/', url or '')
    s = m.group(1) if m else ""
    return CIRC.get(s, (slug(s or name), name or s))

def _rows_for(results, box, grid_by_num, total_laps):
    fl_name = (box.get("fastest_lap") or {}).get("name", "") if box else ""
    fl_sur = slug(fl_name.split()[-1]) if fl_name else None
    out = []
    win_t = None
    for r in results:
        t = t2s(r.get("race_time", ""))
        if win_t is None and t is not None and str(r.get("position", "")).isdigit():
            win_t = t
    for i, r in enumerate(results):
        num = str(r.get("driver_number") or "")
        name = r.get("driver_name", "")
        d = did(name); row_sur = surname_slug(name)
        pos = str(r.get("position") or "").strip()
        fin = pos.isdigit()
        rtime = (r.get("race_time") or "").strip()
        t = t2s(rtime)
        # grid from position+movement when both numeric, else quali grid
        mv = r.get("movement")
        grid = ""
        if fin and isinstance(mv, int):
            grid = str(int(pos) + mv)
        elif num in grid_by_num:
            grid = grid_by_num[num]
        if fin:
            gap = "-" if i == 0 or win_t is None or t is None else (fmt_gap(t - win_t) if t >= win_t else rtime)
            status = "Finished"
        else:
            gap = rtime or pos
            status = "DNF"
        flt = fmt_lap((box.get("fastest_lap") or {}).get("time", "")) if (fl_sur and row_sur == fl_sur) else ""
        out.append({
            "number": num, "driverId": d, "grid": grid,
            "position": pos if fin else "NC",
            "laps": str(total_laps) if fin else "",
            "gap": gap, "status": status,
            "points": str(r.get("points") or "0").strip() or "0",
            "Time": {"time": rtime},
            "FastestLap": {"lap": "0", "Time": {"time": flt}},
        })
    return out

def assemble(year, slug_name, verbose=False):
    cid = champ_id(slug_name)
    races = season_races(cid)
    # attach race_id + box + results + quali to each
    enr = []
    for rc in races:
        rid = post_id(rc["url"])
        box = race_box(rid)
        res = race_results(rid, 0)
        ql = race_quali(rid)
        enr.append({**rc, "rid": rid, "box": box, "res": res, "quali": ql,
                    "date": pdate(rc.get("race_date", "")), "laps_n": int(rc["laps"]) if str(rc.get("laps", "")).isdigit() else 0})
    # group into weekends by date gap >3 days
    weekends, cur = [], []
    for rc in enr:
        if cur and rc["date"] and cur[-1]["date"] and (rc["date"] - cur[-1]["date"]).days > 3:
            weekends.append(cur); cur = []
        cur.append(rc)
    if cur: weekends.append(cur)

    rounds = []
    reg = {}          # driverId -> {(number, constructorId): set(round)}
    team_pts = {}     # constructorId -> float points (summed from race rows)
    team_nm = {}      # constructorId -> {team_name: count}
    dname = {}        # driverId -> full name (from results)
    def capture(res, rno):
        for r in res:
            d = did(r.get("driver_name", "")); num = str(r.get("driver_number") or "")
            c = con(r.get("team_url", "")); pts = float(r.get("points") or 0)
            reg.setdefault(d, {}).setdefault((num, c), set()).add(rno)
            if c:
                team_pts[c] = team_pts.get(c, 0) + pts
                team_nm.setdefault(c, {})[r.get("team_name", "")] = team_nm.setdefault(c, {}).get(r.get("team_name", ""), 0) + 1
            dname.setdefault(d, r.get("driver_name", ""))
    for wi, wk in enumerate(weekends, 1):
        # feature scores more than a sprint: pick by winner points, then laps
        def qn(rc): return len(rc["quali"]) if isinstance(rc["quali"], list) else 0
        def winpts(rc):
            return max((float(x.get("points") or 0) for x in rc["res"]), default=0)
        feat = max(wk, key=lambda rc: (winpts(rc), rc["laps_n"]))
        sprints = [rc for rc in wk if rc is not feat]
        sprints.sort(key=lambda rc: (rc["date"] or datetime.min, rc["round"]))
        grid_q = {str(q.get("driver_number")): str(q.get("grid")) for q in feat["quali"]} if isinstance(feat["quali"], list) else {}
        for rc in wk: capture(rc["res"], wi)
        Results = {}
        if len(sprints) == 1:
            Results["race1"] = _rows_for(sprints[0]["res"], sprints[0]["box"], {}, sprints[0]["laps_n"])
            Results["race2"] = _rows_for(feat["res"], feat["box"], grid_q, feat["laps_n"])
        elif len(sprints) >= 2:
            Results["race1"] = _rows_for(sprints[0]["res"], sprints[0]["box"], {}, sprints[0]["laps_n"])
            Results["race2"] = _rows_for(sprints[1]["res"], sprints[1]["box"], {}, sprints[1]["laps_n"])
            Results["race3"] = _rows_for(feat["res"], feat["box"], grid_q, feat["laps_n"])
        else:
            Results["race2"] = _rows_for(feat["res"], feat["box"], grid_q, feat["laps_n"])
        circ_id, race_name = circuit_of(feat.get("circuit_url", ""), feat.get("circuit_name", ""))
        rounds.append({"season": str(year), "round": str(wi),
                       "raceName": race_name,
                       "Circuit": {"circuitId": circ_id, "circuitName": feat.get("circuit_name", "")},
                       "Results": Results,
                       "_feat_laps": feat["laps_n"], "_nsprint": len(sprints),
                       "_date": feat.get("race_date", "")})
        if verbose:
            print(f"  WK{wi:2d} {feat.get('circuit_name',''):16s} races={len(wk)} sprints={len(sprints)} "
                  f"featLaps={feat['laps_n']} qrows={qn(feat)} sprintQrows={[qn(s) for s in sprints]} {feat.get('race_date','')}")
    extra = {"reg": reg, "team_pts": team_pts, "team_nm": team_nm, "dname": dname}
    return rounds, races, season_standings(cid), cid, extra

def reconcile(year, slug_name, verbose=True):
    rounds, races, standings, cid, extra = assemble(year, slug_name, verbose=verbose)
    # sum points per driverId from race rows
    got = {}
    for rd in rounds:
        for rows in rd["Results"].values():
            for x in rows:
                got[x["driverId"]] = got.get(x["driverId"], 0) + float(x["points"] or 0)
    print(f"\n{year} ({slug_name}) cid={cid}: {len(races)} races -> {len(rounds)} rounds; drivers in results={len(got)}")
    print("  standings n=", len(standings))
    bad = 0
    for s in standings:
        d = did(s["driver_name"]); off = float(s["points"]) - got.get(d, 0)
        if abs(off) > 0.001:
            bad += 1
            if verbose and bad <= 15:
                print(f"    MISMATCH {s['driver_name']:24s} id={d:14s} official={s['points']:>6} summed={got.get(d,0):g} off={off:+g}")
    print(f"  reconcile: {len(standings)-bad}/{len(standings)} drivers match" + (" ✅" if bad == 0 else f"  ❌ {bad} off"))
    return rounds, standings

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "probe"
    def slug_for(y):
        return f"{y}-gp2-series" if y <= 2016 else f"{y}-fia-formula-2-championship"
    if cmd == "teams":
        years = [int(a) for a in sys.argv[2:]] or [2016]
        seen = {}
        for y in years:
            _, races, st, cid, extra = assemble(y, slug_for(y))
            for s in st:
                seen.setdefault(team_slug(s.get("team_url", "")), con(s.get("team_url", "")))
        for s, c in sorted(seen.items()): print(f"  {s:34s} -> {c}")
    else:
        y = int(sys.argv[2]) if len(sys.argv) > 2 else 2016
        reconcile(y, slug_for(y))
