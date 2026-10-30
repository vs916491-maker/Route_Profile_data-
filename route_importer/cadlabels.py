"""Profiles found from their own labels, line (UP/DN) detection, stations from
the whole drawing, and assembling one route from many drawings.

Works for CAD L-sections that have no ruled band table and no chainage tick
row: every profile label carries its chainage ('PVI = 0+194.020',
'CHAINAGE=4365.577 ELEVATION=192.22', 'TS: 3666.337' ...), and on a profile the
labels line up with chainage along x.  Such a row of labels is a profile strip.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

import numpy as np

from . import cadprofile as cp
from .bands import SheetData
from .portals import route_tunnels, sheet_portals
from .sources import Sheet

_UP = re.compile(r"\bULC\b|\bUP\s*-?\s*LINE\b|\(\s*UP\s*\)|\bUP\s*LINE\s*CURVE|\bUP\b", re.I)
_DN = re.compile(r"\bDLC\b|\b(?:DN|DOWN)\s*-?\s*LINE\b|\(\s*(?:DN|DOWN)\s*\)|\bDN\b|\bDOWN\b", re.I)


def tag_direction(text: str) -> str | None:
    up, dn = bool(_UP.search(text)), bool(_DN.search(text))
    if up and not dn:
        return "UP"
    if dn and not up:
        return "DN"
    return None


# ------------------------------------------------------------ calibration
class LinCal:
    """chainage = a + b * x (one profile segment)."""

    def __init__(self, a, b, xs, cs):
        self.a, self.b = a, b
        self.xs, self.cs = np.array(sorted(xs)), np.array([a + b * x for x in sorted(xs)])
        self.bad = []

    ok = True

    def __call__(self, x):
        return float(self.a + self.b * x)


def _anchors(sheet: Sheet):
    out = []
    for it in sheet.items:
        h = min(it.w, it.h)
        if h < 0.5:                       # tiny annotation / table text
            continue
        t = it.text
        m = cp._PVI.match(t) or cp._PVCT.match(t) or cp._HPT.match(t)
        kind = None
        if cp._PVI.match(t) or cp._PVI_COMBO.search(t):
            kind = "V"
            mm = cp._PVI.match(t)
            v = cp.ch_value(mm.group(1)) if mm else cp.ch_value(cp._PVI_COMBO.search(t).group(1))
        elif m:
            kind = "V" if cp._PVCT.match(t) else "H"
            v = cp.ch_value(m.groups()[-1])
        else:
            continue
        if v is not None:
            out.append((it, v, kind))
    return out


def _clusters(items, Dx, Dy=None):
    Dy = Dx if Dy is None else Dy
    grid = {}
    for i, (it, _, _) in enumerate(items):
        grid.setdefault((int(it.x // Dx), int(it.y // Dy)), []).append(i)
    par = list(range(len(items)))

    def f(a):
        while par[a] != a:
            par[a] = par[par[a]]
            a = par[a]
        return a
    for (gx, gy), L in grid.items():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((gx + dx, gy + dy), []):
                    for i in L:
                        a, b = items[i][0], items[j][0]
                        if abs(a.x - b.x) < Dx and abs(a.y - b.y) < Dy:
                            par[f(i)] = f(j)
    g = {}
    for i in range(len(items)):
        g.setdefault(f(i), []).append(i)
    return list(g.values())


def _fit(points, tol):
    """RANSAC line ch = a + b x over (x, ch)."""
    best = None
    pts = np.array(points)
    n = len(pts)
    rng = np.random.default_rng(0)
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)] if n <= 40 else \
        [tuple(rng.choice(n, 2, replace=False)) for _ in range(800)]
    for i, j in pairs:
        dx = pts[j, 0] - pts[i, 0]
        if abs(dx) < 1e-9:
            continue
        b = (pts[j, 1] - pts[i, 1]) / dx
        if not (0.02 <= abs(b) <= 50):
            continue
        a = pts[i, 1] - b * pts[i, 0]
        inl = np.abs(pts[:, 1] - (a + b * pts[:, 0])) <= tol
        if best is None or inl.sum() > best[0]:
            best = (int(inl.sum()), inl)
    if best is None or best[0] < 3:
        return None
    inl = best[1]
    b, a = np.polyfit(pts[inl, 0], pts[inl, 1], 1)
    return a, b, inl


def find_label_strips(sheet: Sheet, existing: list) -> list:
    anchors = _anchors(sheet)
    if len(anchors) < 6:
        return []
    h0 = float(np.median([min(it.w, it.h) for it, _, _ in anchors]))
    strips = []
    for c in _clusters(anchors, 160 * h0, 200 * h0):
        rest = [anchors[i] for i in c]
        while len(rest) >= 6 and sum(1 for _, _, k in rest if k == "V") >= 3:
            grp = rest
            # inside an existing tick-row strip? then it is already read
            if any(e.x0 - 80 <= np.median([it.x for it, _, _ in grp]) <= e.x1 + 80 and
                   abs(np.median([it.y for it, _, _ in grp]) - e.y) < 320 for e in existing):
                break
            fit = _fit([(it.x, v) for it, v, _ in grp], tol=max(2.0, 1.0 * h0))
            if fit is None:
                break
            a, b, inl = fit
            good = [g for g, ok in zip(grp, inl) if ok]
            rest = [g for g, ok in zip(grp, inl) if not ok]       # other sheets of the same group
            if len(good) < 5 or sum(1 for _, _, k in good if k == "V") < 2:
                continue
            xs = [it.x for it, _, _ in good]
            ys = [it.y for it, _, _ in good]
            st = cp.Strip(float(np.median(ys)), min(xs), max(xs), LinCal(a, b, xs, [v for _, v, _ in good]), 10.0)
            st.ylo, st.yhi = min(ys) - 25 * h0, max(ys) + 25 * h0
            st.h0 = h0
            st.label_strip = True
            st.anchor_ids = {id(it) for it, _, _ in good}
            strips.append(st)
    return strips


def assign_label_strips(sheet: Sheet, strips: list):
    for it in sheet.items:
        own = [s for s in strips if id(it) in s.anchor_ids]
        if own:
            own[0].items.append(it)
            continue
        for s in strips:
            if s.x0 - 40 * s.h0 <= it.x <= s.x1 + 40 * s.h0 and s.ylo <= it.y <= s.yhi:
                s.items.append(it)
                break


# --------------------------------------------------------- line detection
def vote_direction(sheet: Sheet, s, values: list[float]) -> tuple[str | None, int, int]:
    """Evidence for UP / DN of a strip: its own title, else the same chainage labels
    elsewhere in the drawing (plan, tables) next to ULC/DLC, 'UP LINE', '(DN)' ...
    Returns (title direction, UP votes, DN votes)."""
    titles = [it for it in s.items if cp._TITLE.search(it.text) and len(it.text) < 45]
    for t in sorted(titles, key=lambda it: -min(it.w, it.h)):
        dname = tag_direction(t.text)
        if dname:
            return dname, 0, 0
    keys = {f"{v:.3f}" for v in values}
    inside = {id(it) for it in s.items}
    votes = {"UP": 0, "DN": 0}
    hits = [it for it in sheet.items if id(it) not in inside and
            any(k in it.text.replace("+", "").replace(" ", "") for k in keys if len(k) >= 6)]
    toks = [it for it in sheet.items if tag_direction(it.text)]
    for it in hits:
        own = tag_direction(it.text)
        if own:
            votes[own] += 2
            continue
        r = 25 * max(min(it.w, it.h), 0.5)
        near = [o for o in toks if abs(o.x - it.x) < r and abs(o.y - it.y) < r]
        if near:
            o = min(near, key=lambda o: abs(o.x - it.x) + abs(o.y - it.y))
            votes[tag_direction(o.text)] += 1
    return None, votes["UP"], votes["DN"]


def decide_lines(strips_votes):
    """Strips in the same drawn row are one line. A row takes its title, else the majority
    of votes; a row without evidence next to a decided parallel row gets the other line."""
    rows = []
    for s, v in strips_votes:
        for r in rows:
            if abs(r[0][0].y - s.y) < 60 * s.h0:
                r.append((s, v)); break
        else:
            rows.append([(s, v)])
    dec = []
    for r in rows:
        titled = [v[0] for _, v in r if v[0]]
        up = sum(v[1] for _, v in r); dn = sum(v[2] for _, v in r)
        if titled:
            d = max(set(titled), key=titled.count); why = "title"
        elif up != dn:
            d = "UP" if up > dn else "DN"; why = f"labels elsewhere in the drawing (UP {up} / DN {dn})"
        else:
            d, why = None, "no evidence"
        dec.append([r, d, why])
    for r in dec:
        if r[1] is None:
            lo = min(min(s.cal(s.x0), s.cal(s.x1)) for s, _ in r[0])
            hi = max(max(s.cal(s.x0), s.cal(s.x1)) for s, _ in r[0])
            def ov(o):
                olo = min(min(s.cal(s.x0), s.cal(s.x1)) for s, _ in o[0])
                ohi = max(max(s.cal(s.x0), s.cal(s.x1)) for s, _ in o[0])
                return max(0.0, min(hi, ohi) - max(lo, olo)) / max(1.0, min(hi - lo, ohi - olo))
            partners = [o for o in dec if o is not r and o[1] and ov(o) >= 0.5]
            ds = {o[1] for o in partners}
            if len(ds) == 1:
                r[1] = "DN" if ds == {"UP"} else "UP"
                r[2] = "parallel to the " + ds.pop() + " row"
    out = {}
    for r, d, why in dec:
        for s, _ in r:
            out[id(s)] = (d, why)
    return out


def sheet_pvis(sheet: Sheet) -> list[tuple[float, float]]:
    """Every PVI with its level in the drawing (also labels outside any profile strip)."""
    rls = [it for it in sheet.items if cp._RL.match(it.text)]
    out = []
    for it in sheet.items:
        m = cp._PVI.match(it.text)
        if m:
            h = max(min(it.w, it.h), 0.5)
            near = [o for o in rls if abs(o.x - it.x) < 5 * h and abs(o.y - it.y) < 9 * h]
            ch = cp.ch_value(m.group(1))
            if near and ch is not None:
                o = min(near, key=lambda o: abs(o.x - it.x) + abs(o.y - it.y))
                out.append((ch, float(cp._RL.match(o.text).group(1))))
            continue
        m = cp._PVI_COMBO.search(it.text)
        if m:
            ch = cp.ch_value(m.group(1))
            if ch is not None:
                out.append((ch, float(m.group(2))))
    return sorted(set((round(c, 3), z) for c, z in out))


# ---------------------------------------------------------- radius pool
def radius_pool(sheet: Sheet, tables: list[dict]) -> list[tuple[float, float]]:
    """(R, circular length) pairs from plan labels and curve tables, to find a curve's
    radius from its exact SC..CS length."""
    pool = []
    rx = [re.compile(r"\bR\s*=\s*(-?\d+(?:\.\d+)?)\s*M?\s*,?\s*(?:L|LC)\s*=\s*(\d+(?:\.\d+)?)", re.I),
          re.compile(r"\b(?:L|LC)\s*=\s*(\d+(?:\.\d+)?)\s*M?\s*,?\s*R\s*=\s*(-?\d+(?:\.\d+)?)", re.I)]
    texts = [it.text for it in sheet.items]
    tails = [it for it in sheet.items if re.match(r"^\s*=\s*\d", it.text)]
    for it in sheet.items:
        if re.search(r"(?:\bL|\bLc|\bLC)\s*$", it.text) and re.search(r"\bR\s*=", it.text):
            h = max(min(it.w, it.h), 0.5)
            t2 = [o for o in tails if abs(o.x - it.x) < 1.5 * h and abs(o.y - it.y) < 1.5 * h]
            if t2:
                texts.append(it.text + " " + t2[0].text.strip())
    for text in texts:
        for i, r in enumerate(rx):
            m = r.search(text)
            if m:
                a, b = float(m.group(1)), float(m.group(2))
                R, L = (a, b) if i == 0 else (b, a)
                if 20 <= abs(R) <= 100000 and 1 <= L <= 5000:
                    pool.append((R, L))
                break
    # 'Lc 132.005' and 'R 200.000' written as two texts next to each other
    lcs = [(it, float(m.group(1))) for it in sheet.items
           if (m := re.match(r"^\s*L\s*c\s*[=:]?\s*(\d+(?:\.\d+)?)\s*M?\s*$", it.text, re.I))]
    rs = [(it, float(m.group(1))) for it in sheet.items
          if (m := re.match(r"^\s*R\s*[=:]?\s*(-?\d+(?:\.\d+)?)\s*M?\s*$", it.text, re.I))]
    for it, L in lcs:
        h = max(min(it.w, it.h), 0.5)
        near = [(abs(o.x - it.x) + abs(o.y - it.y), R) for o, R in rs if abs(o.x - it.x) < 4 * h and abs(o.y - it.y) < 4 * h]
        if near:
            R = min(near)[1]
            if 20 <= abs(R) <= 100000:
                pool.append((R, L))
    for t in tables:
        if isinstance(t.get("radius"), float) and isinstance(t.get("lc"), float):
            pool.append((t["radius"], t["lc"]))
    return pool


# ------------------------------------------------------------- stations
_STN_CH = re.compile(r"(UP|DN|DOWN)?\s*(?:LINE)?\s*CH\s*\.?\s*[:=]?\s*" + cp._CH + r"\s*M?\s*(\(\s*(?:UP|DN|DOWN)(?:\s*LINE)?\s*\)|UP|DN|DOWN)?",
                     re.I)


_NOISE_NAME = re.compile(r"MATCH\s*LINE|CROSS\s*SECTION|NALA|FOOTPATH|MEDIAN|TUNNEL|\bROOM\b|BLOCK|^PARK$|\bCP-|\bCC-|"
                         r"\bBH-|LENGTH|CONTRACT|NATM|BUILD|ROAD|JHUGGI|GROUND|^DELHI$|\bAREA\b|SHAFT|RAMP|PORTAL|"
                         r"BOUNDARY|M E H|A G S|SURVEY|BORE|DRAIN|SEWER|PIPE|CABLE|TOWER|TEMPLE|SCHOOL|HOSPITAL BLOCK|"
                         r"GATE|WALL|FLYOVER|BRIDGE|CANAL|RIVER|TRACK|TURNOUT|CROSS\s*OVER|LIFT|STAIR|ENTRY|EXIT",
                         re.I)


def sheet_stations(sheet: Sheet) -> list[dict]:
    """Station labels anywhere in the drawing: a station name next to a chainage label
    ('UP LINE CH: 22934.162 m', 'CH:5124.633 (DN LINE)', 'CH: 22823.053', 'CH:28518.148 UP')."""
    names = [it for it in sheet.items
             if re.fullmatch(r"[A-Za-z][A-Za-z0-9 .\-/&()']{2,40}", it.text.strip())
             and len(re.sub(r"[^A-Za-z]", "", it.text)) >= 3 and not cp._NOT_STATION.search(it.text)
             and not _NOISE_NAME.search(it.text.strip())
             and not cp._TITLE.search(it.text) and not re.search(r"\d{3}", it.text)
             and not re.fullmatch(r"[A-Za-z]{1,4}\s*-?\s*\d+[A-Za-z]?", it.text.strip())]
    out = []
    for it in sheet.items:
        if "CH" not in it.text.upper() or len(it.text) > 60:
            continue
        for m in _STN_CH.finditer(it.text):
            ch = cp.ch_value(m.group(2))
            if ch is None:
                continue
            dn = tag_direction((m.group(1) or "") + " " + (m.group(3) or "") + " " + it.text)
            h = max(min(it.w, it.h), 0.5)
            cands = [o for o in names if abs(o.x - it.x) < 12 * h and abs(o.y - it.y) < 6 * h
                     and 0.6 * h <= min(o.w, o.h) <= 4 * h]
            if not cands:
                continue
            o = min(cands, key=lambda o: abs(o.y - it.y) + 0.3 * abs(o.x - it.x))
            nm = re.sub(r"\b(STN\.?|STATION|U/G|E/V|EXISTING|PROPOSED|PLATFORM CENTER|CENTRE|CENTER|OF)\b|C/L", "",
                        o.text, flags=re.I).strip(" .-")
            nm = re.sub(r"\s+", " ", nm).upper()
            if len(re.sub(r"[^A-Z]", "", nm)) >= 3:
                out.append({"name": nm, "ch": ch, "dir": dn, "src": sheet.label})
    # unique by (name, rounded ch, dir)
    seen, uniq = set(), []
    for s in out:
        k = (s["name"], round(s["ch"], 1), s["dir"])
        if k not in seen:
            seen.add(k); uniq.append(s)
    return uniq


# --------------------------------------------------------------- reading
def interpret_sheet(sheet: Sheet, log=print) -> list[SheetData]:
    """Tick-row strips (cadprofile) + label strips, with line detection and stations."""
    tick = cp.find_strips(sheet)
    cp.assign(sheet, tick)
    lab = find_label_strips(sheet, tick)
    assign_label_strips(sheet, lab)
    tables = cp.read_curve_tables(sheet)
    pool = radius_pool(sheet, tables)
    stations = sheet_stations(sheet) if lab else []
    out = []
    datas = {}
    for s in tick + lab:
        datas[id(s)] = cp.interpret_strip(sheet, s, tables, pool)
    votes = []
    for s in lab:
        d = datas[id(s)]
        vals = [p for g in d.grades for p in (g["from"], g["to"])] + \
               [a["sc"] for a in d.arcs] + [a["cs"] for a in d.arcs]
        votes.append((s, vote_direction(sheet, s, vals)))
    lines = decide_lines(votes) if votes else {}
    known = {v[0] for v in lines.values() if v[0]}
    if len(known) == 1:              # the drawing shows one line only: unmarked strips are that line
        only = known.pop()
        for s in lab:
            if lines.get(id(s), (None,))[0] is None:
                lines[id(s)] = (only, f"the only line in this drawing ({only})")
    for s in tick + lab:
        d = datas[id(s)]
        if getattr(s, "label_strip", False):
            dirn, why = lines.get(id(s), (None, "no evidence"))
            name = f"{dirn or 'Line'} profile {d.ch_min:.0f}-{d.ch_max:.0f}"
            d.sheet.name = name
            d.sheet.is_depot = False
            d.direction = dirn
            d.info.append(f"{name}: line {dirn or 'unknown'} from {why}")
            d.stations = [dict(st) for st in stations
                          if d.ch_min - 50 <= st["ch"] <= d.ch_max + 50 and st["dir"] in (dirn, None)]
        else:
            d.direction = cp.direction_of(s.title)
            d.trusted_stations = True          # titled strip: station labels next to the profile
        if d.ok:
            out.append(d)
    spv = sheet_pvis(sheet)
    marks = sheet_portals(sheet)
    for d in out:
        d.sheet_pvis = spv
        d.sheet_portals = marks
    out = drop_duplicate_systems(out, log)
    for d in out:
        log(f"  profile '{d.sheet.name}': {d.ch_min:.0f}..{d.ch_max:.0f} m, {len(d.grades)} grades, "
            f"{len(d.vcurves)} vertical curves, {len(d.arcs)} curves, {len(d.stations)} stations"
            + ("" if not d.sheet.is_depot else " (not a main line)"))
    return out


def _level_map(d, e):
    """Map chainages of profile d onto profile e when both show the same PVI levels in a
    different chainage system. Returns (matched share, function) or (0, None)."""
    zd = [(g["from"], g.get("z_from")) for g in d.grades if g.get("z_from") is not None]
    ze = [(g["from"], g.get("z_from")) for g in e.grades if g.get("z_from") is not None]
    if len(zd) < 5 or len(ze) < 5:
        return 0.0, None
    offs = [ce - cd for cd, a in zd for ce, b in ze if abs(a - b) < 0.002]
    if not offs:
        return 0.0, None
    med = float(np.median(offs))
    if abs(med) < 100:
        return 0.0, None
    pairs = []
    for cd, a in zd:
        c = [ce for ce, b in ze if abs(a - b) < 0.002 and abs((ce - cd) - med) < 60]
        if c:
            pairs.append((cd, min(c, key=lambda ce: abs((ce - cd) - med))))
    share = len(pairs) / len(zd)
    if share < 0.7:
        return share, None
    pairs.sort()
    xs = np.array([p[0] for p in pairs]); ys = np.array([p[1] for p in pairs])

    def f(c):
        if c <= xs[0]:
            return float(ys[0] + (c - xs[0]))
        if c >= xs[-1]:
            return float(ys[-1] + (c - xs[-1]))
        return float(np.interp(c, xs, ys))
    return share, f


def transfer_stations(datas, log=print):
    """Profiles from different drawings that overlap at a junction can use different chainage
    systems (e.g. Phase-4 66045 = Phase-3 -342 at Majlis Park). Where at least two PVIs have
    the same level and a constant offset, stations of one are carried into the other."""
    for d in datas:
        zd = [(g["from"], g.get("z_from")) for g in d.grades if g.get("z_from") is not None]
        zmap = dict(zd)
        for e in datas:
            if e is d or not d.stations or e.sheet.source == d.sheet.source:
                continue
            ze = [(g["from"], g.get("z_from")) for g in e.grades if g.get("z_from") is not None]
            pairs = [(cd, ce) for cd, a in zd for ce, b in ze if abs(a - b) < 0.002 and abs(ce - cd) > 100]
            if len(pairs) < 3:
                continue
            offs = np.array([ce - cd for cd, ce in pairs])
            med = float(np.median(offs))
            good = [(cd, ce) for cd, ce in pairs if abs((ce - cd) - med) < 3]
            if len(good) < 3 or len({round(zmap[cd], 2) for cd, _ in good}) < 2:
                continue
            lo = min(cd for cd, _ in good) - 500
            hi = max(cd for cd, _ in good) + 500
            moved = 0
            for st in d.stations:
                if lo <= st["ch"] <= hi:
                    ch = st["ch"] + med
                    if e.ch_min - 50 <= ch <= e.ch_max + 50 and \
                            not any(abs(ch - x["ch"]) < 20 for x in e.stations):
                        e.stations.append(dict(st, ch=round(ch, 3), src=st.get("src", "") + f" (shifted {med:+.1f} m)"))
                        moved += 1
            if moved:
                e.info.append(f"{moved} station(s) from '{d.sheet.name}' mapped at a junction "
                              f"(chainage systems differ by {med:+.1f} m)")
                log(f"  junction: {moved} station(s) of '{d.sheet.name}' mapped into '{e.sheet.name}' ({med:+.1f} m)")


def drop_duplicate_systems(datas, log=print):
    """A drawing may show the same profile twice: in the route chainage and in a local one
    (e.g. 0+000 at Maujpur). Same PVI levels, chainages shifted. Keep the copy in the larger
    chainage system; carry the dropped copy's stations over (mapped through matched PVIs)."""
    drop = set()
    for d in datas:
        targets = []
        for e in datas:
            if e is d:
                continue
            share, f = _level_map(d, e)
            if f is not None and abs(e.ch_max) > abs(d.ch_max):
                targets.append((e, f))
        if not targets:
            continue
        drop.add(id(d))
        for e, f in targets:
            for st in d.stations:
                ch = f(st["ch"])
                if not any(abs(ch - x["ch"]) < 5 and x["name"] == st["name"] for x in e.stations):
                    e.stations.append(dict(st, ch=round(ch, 3), src=st.get("src", "") + " (mapped)"))
            e.info.append(f"stations taken from '{d.sheet.name}' (same profile in another chainage system)")
        log(f"  '{d.sheet.name}' repeats '{targets[0][0].sheet.name}' in another chainage system - "
            f"its stations are carried over, the copy is not used")
    return [d for d in datas if id(d) not in drop]


# ------------------------------------------------------------- route
def _norm(n):
    n = re.sub(r"\b(STATION|STN|METRO|U/G|E/V)\b", " ", n.upper())
    return re.sub(r"[^A-Z0-9]", "", n)


def name_score(a, b):
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    r = difflib.SequenceMatcher(None, na, nb).ratio()
    if len(na) >= 4 and len(nb) >= 4 and (na.startswith(nb) or nb.startswith(na)):
        r = max(r, 0.85)
    return r


def match_station(name, stations, aliases):
    cands = [name] + list(aliases.get(name.upper().strip(), []))
    best = None
    for st in stations:
        for c in cands:
            r = name_score(c, st["name"])
            if best is None or r > best[0]:
                best = (r, st)
    return best[1] if best and best[0] >= 0.72 else None


@dataclass
class Piece:
    data: SheetData | None       # None = gap (no profile of this line)
    lo: float
    hi: float


def parse_route(text: str) -> list[tuple[str, float | None]]:
    """'MUKUNDPUR > LAJPAT NAGAR > ...' or one station per line; 'NAME = 66045' fixes a
    chainage the drawings do not show."""
    out = []
    text = "\n".join(ln.split("#")[0] for ln in (text or "").splitlines())    # '# comments'
    for part in re.split(r">|\n|;", text):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(.*?)\s*[=@]\s*(-?\d+(?:\.\d+)?)\s*$", part)
        out.append((m.group(1).strip().upper(), float(m.group(2))) if m else (part.upper(), None))
    return out


def assemble(datas: list[SheetData], direction: str, route: list, aliases: dict, log=print):
    """One route on one line through the route stations in order. Returns (data, notes)."""
    cands = [d for d in datas if d.ok and not d.sheet.is_depot]
    exact = [d for d in cands if getattr(d, "direction", None) == direction]
    other = [d for d in cands if getattr(d, "direction", None) is None]
    opposite = [d for d in cands if d not in exact and d not in other]
    pool = exact + other + opposite
    if not pool:
        return None, ["no profile found"]
    notes = []
    rank = {id(d): (2 if d in exact else 1 if d in other else 0) for d in pool}
    st_all = [dict(st, prof=d) for d in pool for st in d.stations]
    # ---- resolve the route stations, in increasing chainage
    found, last = [], -1e12
    for name, fixed in route:
        if fixed is not None:
            found.append({"name": name, "ch": fixed, "src": "route list (given chainage)", "rank": 3})
            last = fixed
            continue
        best = None
        for st in st_all:
            if st["ch"] <= last + 1:
                continue
            sc = max([name_score(c, st["name"]) for c in [name] + list(aliases.get(name, []))])
            if sc < 0.72:
                continue
            key = (round(sc, 1), rank[id(st["prof"])], -st["ch"])
            if best is None or key > best[0]:
                best = (key, st)
        if best is None:
            notes.append(f"station '{name}' not found in the drawings after {last:.0f} m")
            continue
        st = best[1]
        found.append({"name": name, "ch": st["ch"], "src": f"{st.get('src', '')} as '{st['name']}'",
                      "rank": rank[id(st["prof"])]})
        last = st["ch"]
    if len(found) < 2:
        return None, notes + ["need at least a start and an end station that the drawings show"]
    if route and found[-1]["name"] != route[-1][0]:
        # the last station is not in the drawings: run to the end of the data
        tail = [d for d in pool if d.ch_max > found[-1]["ch"]]
        if tail:
            endc = max(d.ch_max for d in tail if rank[id(d)] == max(rank[id(x)] for x in tail))
            notes.append(f"route runs on to the end of the drawings ({endc:.0f} m); give "
                         f"'{route[-1][0]} = <chainage>' in the route list to end it at that station")
            found.append({"name": "END OF DRAWINGS", "ch": endc, "src": "end of profile data", "rank": 0})
    start, end = found[0]["ch"], found[-1]["ch"]
    wps = [f["ch"] for f in found]
    # ---- sweep along the route
    pieces, p, cur = [], start, None
    for _ in range(500):
        if p >= end - 1e-6:
            break
        cover = [d for d in pool if d.ch_min - 1e-6 <= p < d.ch_max - 1e-6]
        if cur is not None and cur in cover and rank[id(cur)] == max(rank[id(d)] for d in cover):
            nxt = cur
        elif cover:
            nwp = next((c for c in wps if c > p + 1), end)

            def off_route(d):       # a branch: it has (reliable) stations the route does not stop at
                if not getattr(d, "trusted_stations", False):
                    return False
                # on the route = a route station of that name was placed near this chainage
                # (a name match alone is not enough: 'SHIV VIHAR' looks like 'SONIA VIHAR')
                return any(p < st["ch"] < d.ch_max - 1 and
                           not any(abs(f["ch"] - st["ch"]) < 400 and
                                   max([name_score(c, st["name"]) for c in [f["name"]] + list(aliases.get(f["name"], []))])
                                   >= 0.72 for f in found)
                           for st in d.stations)
            ok = [d for d in cover if not off_route(d)]
            if not ok:
                later = [d for d in pool if d.ch_min > p and not off_route(d)]
                q = min([d.ch_min for d in later] + [end])
                notes.append(f"GAP {p:.0f}..{q:.0f} m: only a branch profile here - grade bridged from the levels either side")
                pieces.append(Piece(None, p, q))
                p, cur = q, None
                continue
            def has_grade(d):
                return any(g.get("from") is not None and g["from"] - 1 <= p < g["to"] - 1 for g in d.grades)
            nxt = max(ok, key=lambda d: (rank[id(d)], has_grade(d), d.ch_max >= nwp - 1,
                                         any(abs(st["ch"] - nwp) < 5 for st in d.stations), d.ch_max))
        else:
            later = [d for d in pool if d.ch_min > p]
            if not later:
                notes.append(f"GAP {p:.0f}..{end:.0f} m: no profile")
                pieces.append(Piece(None, p, end))
                break
            q = min(d.ch_min for d in later)
            notes.append(f"GAP {p:.0f}..{q:.0f} m: no profile in the drawings - grade bridged from the levels either side")
            pieces.append(Piece(None, p, q))
            p, cur = q, None
            continue
        # a better-ranked profile may start inside this one: hand over there
        hi = min(nxt.ch_max, end)
        better = [d.ch_min for d in pool if rank[id(d)] > rank[id(nxt)] and p < d.ch_min < hi]
        if better:
            hi = min(better)
        pieces.append(Piece(nxt, p, hi))
        p, cur = hi, nxt
    merged = []
    for pc in pieces:
        if merged and merged[-1].data is pc.data and abs(merged[-1].hi - pc.lo) < 1e-6:
            merged[-1].hi = pc.hi
        else:
            merged.append(pc)
    for pc in merged:
        if pc.data is not None and rank[id(pc.data)] < 2:
            notes.append(f"{pc.lo:.0f}..{pc.hi:.0f} m taken from '{pc.data.sheet.name}' "
                         f"({'line not marked' if rank[id(pc.data)] == 1 else 'other line'} - no {direction} profile there)")
    out = cut_and_join(merged)
    used = [pc for pc in merged if pc.data is not None]
    # tunnels from the structure marks (START OF TBM, END OF ELEVATED RAMP ...)
    spans = {}
    for pc in used:
        f = re.split(r"[\\/]", pc.data.sheet.source)[-1]
        a, b = spans.get(f, (pc.lo, pc.hi))
        spans[f] = (min(a, pc.lo), max(b, pc.hi))
    marks, seen = [], set()
    for d in pool:
        for m in getattr(d, "sheet_portals", []):
            key = (m["file"], round(m["ch"], 1), m["text"])
            if key not in seen:
                seen.add(key); marks.append(m)
    out.tunnels, tnotes = route_tunnels(marks, direction, start, end, spans)
    out.tunnel_notes = tnotes
    out.info = [f"Route {found[0]['name']} > ... > {found[-1]['name']} on the {direction} line: " +
                "; ".join(f"{pc.data.sheet.source.split('/')[-1].split(chr(92))[-1]} '{pc.data.sheet.name}' "
                          f"{pc.lo:.0f}..{pc.hi:.0f} m" for pc in used)] + \
               [n for n in notes if not n.startswith("GAP")]
    out.warnings += [n for n in notes if n.startswith("GAP")]
    out.stations = [{"name": f["name"], "ch": f["ch"], "src": f["src"]} for f in found]
    return out, notes


def _z_at(d, ch):
    best = None
    for g in d.grades:
        if g.get("z_from") is None:
            continue
        if g["from"] - 1e-6 <= ch <= g["to"] + 1e-6:
            return g["z_from"] + g["permil"] / 1000 * (ch - g["from"])
        dist = min(abs(ch - g["from"]), abs(ch - g["to"]))
        if best is None or dist < best[0]:
            z = g["z_from"] + g["permil"] / 1000 * (ch - g["from"])
            best = (dist, z)
    return best[1] if best and best[0] < 400 else None


def cut_and_join(pieces: list[Piece]) -> SheetData:
    first = next(pc.data for pc in pieces if pc.data is not None)
    out = SheetData(first.sheet)
    used = [pc.data for pc in pieces if pc.data is not None]
    files = list(dict.fromkeys(re.split(r"[\\/]", d.sheet.source)[-1] for d in used))
    name = (f"route of {len(used)} profiles" if len(used) > 2 else " + ".join(d.sheet.name for d in used))
    out.sheet = Sheet(" + ".join(files), name, first.sheet.width, first.sheet.height)
    for i, pc in enumerate(pieces):
        d, lo, hi = pc.data, pc.lo, pc.hi
        if d is None:            # gap: grades from the levels at both ends (and PVIs in between)
            prev = next((q.data for q in reversed(pieces[:i]) if q.data is not None), None)
            nxt = next((q.data for q in pieces[i + 1:] if q.data is not None), None)
            # the profiles either side may stop showing grades before the sheet edge:
            # start the bridge where the last known grade ends / the next one begins
            if prev is not None and i > 0 and pieces[i - 1].data is prev:
                ends = [g["to"] for g in prev.grades if g.get("z_to") is not None
                        and pieces[i - 1].lo < g["to"] <= lo + 1e-6]
                if ends and lo - max(ends) < 1000:
                    lo = max(ends)          # (the profile has no grades after this anyway)
            if nxt is not None and i + 1 < len(pieces) and pieces[i + 1].data is nxt:
                starts = [g["from"] for g in nxt.grades if g.get("z_from") is not None
                          and hi - 1e-6 <= g["from"] < pieces[i + 1].hi]
                if starts and min(starts) - hi < 1000:
                    hi = min(starts)        # its curves etc. before this are still used
            za = _z_at(prev, lo) if prev else None
            zb = _z_at(nxt, hi) if nxt else None
            if za is not None and zb is not None and hi > lo:
                inner = {}
                for q in (prev, nxt):
                    if q is not None and q.sheet.source in {x.sheet.source for x in (prev, nxt) if x}:
                        for c, z in getattr(q, "sheet_pvis", []):
                            if lo + 1 < c < hi - 1:
                                inner[c] = z
                pts = [(lo, za)] + sorted(inner.items()) + [(hi, zb)]
                for (c1, z1), (c2, z2) in zip(pts, pts[1:]):
                    if c2 - c1 < 1:
                        continue
                    g = (z2 - z1) / (c2 - c1) * 1000
                    out.grades.append({"ch": (c1 + c2) / 2, "permil": round(g, 3), "conf": 0.5,
                                       "raw": f"gap {z1:.3f}->{z2:.3f}", "from": c1, "to": c2, "L": None,
                                       "src": "gap", "z_from": z1, "z_to": z2, "bridged": True})
            continue
        last = i == len(pieces) - 1

        def inside(c, lo=lo, hi=hi, last=last):
            return lo <= c < hi or (last and c == hi)
        for g in d.grades:
            f, t = g.get("from"), g.get("to")
            if f is None or t is None or t <= lo or f >= hi:
                continue
            g = dict(g, **{"from": max(f, lo), "to": min(t, hi)})
            g["ch"] = (g["from"] + g["to"]) / 2
            out.grades.append(g)
        out.vcurves += [v for v in d.vcurves if inside(v.get("vpi") or (v["start"] + v["end"]) / 2)]
        out.arcs += [a for a in d.arcs if inside(a["sc"])]
        out.transitions += [t for t in d.transitions if inside(t["start"])]
        out.rail += [(c, z) for c, z in d.rail if inside(c)]
        out.ground += [(c, z) for c, z in d.ground if inside(c)]
        out.cant += [c for c in d.cant if inside(c["ch"])]
        out.speed += [s for s in d.speed if inside(s["ch"])]
        out.items_used += [u for u in d.items_used if u[3] is None or inside(u[3])]
        for w in d.warnings:
            nums = [float(x) for x in re.findall(r"(?<![\d.])(-?\d{2,6}\.\d+)", w.replace("..", " "))[:2]]
            span = (min(nums), max(nums)) if len(nums) == 2 and "grade" in w else None
            if not nums or (span and span[0] < hi and span[1] > lo) or (not span and inside(nums[0])):
                out.warnings.append(f"{d.sheet.name}: {w}")
    out.ch_min, out.ch_max = pieces[0].lo, pieces[-1].hi
    out.ok = True
    return out
