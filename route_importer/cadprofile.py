"""Labelled CAD profiles (DWG/DXF model space without a ruled band table).

Typical consultant L-sections drawn in model space at 1 unit = 1 m chainage:
  * a row of chainage ticks ('28+300', '28+320', ...) per profile strip
  * 'PVI 28+384.687' + 'RL 196.000'            -> vertical intersection points
  * 'PVC ...' / 'PVT ...' + 'L=52.302 R 3000'   -> vertical curves
  * 'TS / SC / CS / ST / SS / PC / PT 28+439.535' + 'R=-1516.330 L=76.438' + 'LS=25.000'
  * rail-level row (numbers every grid step)
  * station: 'LAJPAT NAGAR' + 'CH:28518.148 UP'
  * strip title: 'UP LINE-1', 'DN LINE-2', 'SIDING LINE ...'
  * optional curve table: CURVE NO / RADIUS / TRANSITION LENGTH / PROPOSED CANT /
    CANT DEFICIENCY / MAXIMUM SPEED  (columns per curve, e.g. ULC-78 / DLC-75)

Each strip becomes one SheetData (see bands.py) so the rest of the tool
(merge, Excel, QA) is unchanged.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from .bands import Calib, Mark, SheetData
from .sources import Sheet

_TICK = re.compile(r"^\s*(-?\d{1,3})\s*\+\s*(\d{3})\s*$")
_CH = r"(-?\d{1,3}\s*\+\s*\d{3}(?:\.\d+)?|\d{3,6}\.\d+)"
_PVI = re.compile(r"^\s*PVI\s*[:=]?\s*" + _CH, re.I)
_PVCT = re.compile(r"^\s*(PVC|PVT|BVC|EVC)\s*[:=]?\s*" + _CH, re.I)
_RL = re.compile(r"^\s*(?:RL|R\.L\.?|ELEV(?:ATION)?|EL)\s*[:=]?\s*(\d{1,4}\.\d+)\s*$", re.I)
_HPT = re.compile(r"^\s*(TS|SC|CS|ST|SS|PC|PT|PCC|PRC|BC|EC|TC|CT)\s*[:=]?\s*" + _CH, re.I)
_HR = re.compile(r"\bR\s*=\s*(-?\d+(?:\.\d+)?)\s*M?\b", re.I)
_HL = re.compile(r"\bL\s*=\s*(\d+(?:\.\d+)?)", re.I)
_LS = re.compile(r"^\s*(?:LS|TL|L\.S\.?)\s*=\s*(\d+(?:\.\d+)?)", re.I)
_VC = re.compile(r"^\s*L\s*=?\s*(\d+(?:\.\d+)?)\s*M?\s*,?\s*R\s*=?\s*(-?\d+(?:\.\d+)?)\s*$", re.I)
_G = re.compile(r"G\s*=?\s*(-?\d+(?:\.\d+)?)\s*%", re.I)
_STCH = re.compile(r"^\s*CH\s*[:.]?\s*" + _CH + r"\s*(UP|DN|DOWN)?\s*$", re.I)
_LEVEL = re.compile(r"^\s*(\d{2,4}\.\d{2,3})\s*$")
_NOT_STATION = re.compile(r"X-?OVER|CROSS\s*OVER|SRJ|TURN\s*OUT|BUFFER|TERMINATION|SIDING|RAKE|BOC|SCISSOR|"
                          r"DIAMOND|POINT|SIGNAL|OVERLAP|FOULING|HEEL|TOE|\bTRACK\b|\bEND\b|\bSTART\b|\bPO\s*\d", re.I)
_TITLE = re.compile(r"\b(UP|DN|DOWN|SIDING|DEPOT|STABLING|LOOP|SHUNT|TEST|INSPECTION)\b.*\bLINE\b|\bLINE\b.*\b(UP|DN|DOWN)\b",
                    re.I)


def ch_value(s: str) -> float | None:
    s = s.replace(" ", "")
    if "+" in s:
        km, m = s.split("+", 1)
        try:
            v = abs(int(km)) * 1000 + float(m)
        except ValueError:
            return None
        return -v if km.startswith("-") else v
    try:
        return float(s)
    except ValueError:
        return None


@dataclass
class Strip:
    y: float
    x0: float
    x1: float
    cal: Calib
    step: float
    items: list = field(default_factory=list)
    title: str = ""
    kind: str = "main"       # main | other


# ----------------------------------------------------------------- strips
def find_strips(sheet: Sheet, min_ticks: int = 15) -> list[Strip]:
    """Chainage tick rows ('28+300', '28+320', ...) on one line = one profile strip."""
    rows: dict[int, list] = {}
    for it in sheet.items:
        m = _TICK.match(it.text)
        if m:
            km, mm = m.groups()
            v = abs(int(km)) * 1000 + int(mm)
            rows.setdefault(round(it.y), []).append((it, -v if km.startswith("-") else v))
    groups: list[list] = []
    last = None
    for k in sorted(rows):
        if last is not None and k - last <= 1:
            groups[-1] += rows[k]
        else:
            groups.append(list(rows[k]))
        last = k
    strips = []
    for g in groups:
        g.sort(key=lambda t: t[0].x)
        # one y can hold several separate profiles (different x ranges): split at jumps
        parts, cur = [], [g[0]]
        for a, b in zip(g, g[1:]):
            if b[0].x - a[0].x > 300 or b[1] < a[1]:
                parts.append(cur); cur = [b]
            else:
                cur.append(b)
        parts.append(cur)
        for p in parts:
            # one tick per chainage value and per x position (drawings often repeat labels)
            seen_v, seen_x, q = set(), [], []
            for it, v in p:
                if v in seen_v or any(abs(it.x - x) < 1e-6 for x in seen_x[-3:]):
                    continue
                seen_v.add(v); seen_x.append(it.x); q.append((it, v))
            p = q
            if len(p) < min_ticks:
                continue
            cal = Calib([Mark(it.x, v, 1.0, it.text) for it, v in p])
            if not cal.ok or len(cal.xs) < min_ticks * 0.8:
                continue
            step = float(np.median(np.diff(sorted({v for _, v in p}))))
            strips.append(Strip(float(np.median([it.y for it, _ in p])), p[0][0].x, p[-1][0].x, cal, step))
    return strips


def assign(sheet: Sheet, strips: list[Strip], reach: float = 320.0):
    """Give every text to the nearest strip that covers its x position."""
    for it in sheet.items:
        best = None
        for s in strips:
            if not (s.x0 - 80 <= it.x <= s.x1 + 80):
                continue
            dy = abs(it.y - s.y)
            if dy <= reach and (best is None or dy < best[0]):
                best = (dy, s)
        if best:
            best[1].items.append(it)
    for s in strips:
        titles = [it for it in s.items if _TITLE.search(it.text) and len(it.text) < 40]
        if titles:
            t = max(titles, key=lambda it: (it.h if it.h < it.w else it.w, -abs(it.y - s.y)))
            s.title = re.sub(r"\s+", " ", t.text.strip())
            s.kind = "main" if re.search(r"\b(UP|DN|DOWN)\b", s.title, re.I) and \
                not re.search(r"SIDING|DEPOT|STABLING|LOOP|SHUNT", s.title, re.I) else "other"
        else:   # untitled strip (siding, depot track, detail): own output, not the main line
            s.title = f"Profile {s.cal(s.x0):.0f}-{s.cal(s.x1):.0f}"
            s.kind = "other"


# ------------------------------------------------------------ curve table
TABLE_ROWS = {
    "no": r"CURVE\s*NO", "radius": r"^RADIUS", "tl": r"TRANSITION\s*LENGTH",
    "cant": r"PROPOSED\s*CANT|^CANT\b|SUPER\s*ELEV", "cd": r"CANT\s*DEFICIENCY",
    "vmax": r"MAX(IMUM)?\.?\s*SPEED|SPEED\s*LIMIT|PERMISSIBLE\s*SPEED",
}


def read_curve_tables(sheet: Sheet) -> list[dict]:
    """Tables with one column per curve; returns records {no, radius, tl, cant, cd, vmax}."""
    out = []
    heads = [it for it in sheet.items if re.search(TABLE_ROWS["no"], it.text, re.I)]
    for hd in heads:
        labels = {}
        for key, pat in TABLE_ROWS.items():
            cands = [it for it in sheet.items if re.search(pat, it.text.strip(), re.I)
                     and abs(it.x - hd.x) < 40 * max(hd.h, 1) and 0 <= it.y - hd.y < 40 * max(hd.h, 1)]
            if cands:
                labels[key] = min(cands, key=lambda it: it.y - hd.y)
        if len(labels) < 3 or "radius" not in labels:
            continue
        x_lab = max(it.x + it.w / 2 for it in labels.values())
        tol = max(hd.h, 1) * 0.9
        # columns: values in the curve-number row
        cols = [it for it in sheet.items if abs(it.y - labels["no"].y) < tol and it.x > x_lab and it is not labels["no"]]
        cols = sorted(cols, key=lambda it: it.x)
        if not cols:
            continue
        gaps = np.diff([c.x for c in cols]) if len(cols) > 1 else np.array([200.0])
        cw = float(np.median(gaps)) if len(gaps) else 200.0
        for c in cols:
            rec = {"no": c.text.strip(), "x": c.x}
            for key, lab in labels.items():
                if key == "no":
                    continue
                vals = [it for it in sheet.items if abs(it.y - lab.y) < tol and abs(it.x - c.x) < cw * 0.5]
                if vals:
                    v = min(vals, key=lambda it: abs(it.x - c.x)).text.strip()
                    m = re.search(r"-?\d+(?:\.\d+)?", v)
                    rec[key] = float(m.group(0)) if m else v
            out.append(rec)
    # remove duplicates (same table read from two heads)
    seen, uniq = set(), []
    for r in out:
        k = (r["no"], r.get("radius"))
        if k not in seen:
            seen.add(k); uniq.append(r)
    return uniq


# ------------------------------------------------------------ strip -> data
def interpret_strip(sheet: Sheet, s: Strip, tables: list[dict]) -> SheetData:
    d = SheetData(sheet)
    d.sheet = Sheet(sheet.source, s.title, sheet.width, sheet.height)   # label per strip
    d.sheet.is_depot = s.kind != "main"
    cal = s.cal
    lo, hi = sorted((cal(s.x0), cal(s.x1)))
    d.ch_min, d.ch_max = lo, hi
    its = s.items

    def near(it, pool, dx, dy):
        c = [o for o in pool if o is not it and abs(o.x - it.x) <= dx and abs(o.y - it.y) <= dy]
        return min(c, key=lambda o: abs(o.x - it.x) + abs(o.y - it.y)) if c else None

    # ---------------------------------------------------------- vertical
    rls = [it for it in its if _RL.match(it.text)]
    pvis = []
    for it in its:
        m = _PVI.match(it.text)
        if not m:
            continue
        ch = ch_value(m.group(1))
        r = near(it, rls, 12, 25)
        if ch is None or r is None:
            continue
        pvis.append({"ch": ch, "z": float(_RL.match(r.text).group(1)), "it": it})
    pvis = sorted({round(p["ch"], 3): p for p in pvis}.values(), key=lambda p: p["ch"])
    pvct = []
    for it in its:
        m = _PVCT.match(it.text)
        if m:
            kind = {"BVC": "PVC", "EVC": "PVT"}.get(m.group(1).upper(), m.group(1).upper())
            v = ch_value(m.group(2))
            if v is not None:
                pvct.append((v, kind))
    pvct = sorted(set(pvct))
    vcs_txt = []
    for it in its:
        m = _VC.match(it.text)
        if m:
            vcs_txt.append((cal(it.x), float(m.group(1)), float(m.group(2)), it))
    gtx = []
    for it in its:
        m = _G.search(it.text)
        if m:
            gtx.append((cal(it.x), float(m.group(1)) * 10.0, it))
    for a, b in zip(pvis, pvis[1:]):
        if b["ch"] - a["ch"] < 1:
            continue
        g = (b["z"] - a["z"]) / (b["ch"] - a["ch"]) * 1000.0
        mid = (a["ch"] + b["ch"]) / 2
        written = [gv for c, gv, _ in gtx if a["ch"] < c < b["ch"]]
        conf = 1.0
        if written and min(abs(w - g) for w in written) > 0.15:
            d.warnings.append(f"grade {a['ch']:.1f}..{b['ch']:.1f}: from PVI levels {g:.3f} permil, "
                              f"written {', '.join(f'{w:.2f}' for w in written)} permil")
        d.grades.append({"ch": mid, "permil": round(g, 3), "conf": conf, "raw": f"PVI levels {a['z']:.3f}->{b['z']:.3f}",
                         "from": a["ch"], "to": b["ch"], "L": None, "src": d.sheet.label})
        d.items_used.append(("vertical", a["it"], f"PVI {a['ch']:.3f} RL {a['z']:.3f}", a["ch"]))
    if pvis:
        d.items_used.append(("vertical", pvis[-1]["it"], f"PVI {pvis[-1]['ch']:.3f} RL {pvis[-1]['z']:.3f}", pvis[-1]["ch"]))
    # vertical curves: PVC..PVT around each PVI
    for p in pvis[1:-1] if len(pvis) > 2 else []:
        before = [c for c, k in pvct if k == "PVC" and p["ch"] - 400 < c <= p["ch"]]
        after = [c for c, k in pvct if k == "PVT" and p["ch"] <= c < p["ch"] + 400]
        txt = [v for v in vcs_txt if abs(v[0] - p["ch"]) < 60]
        L = R = None
        if txt:
            t = min(txt, key=lambda v: abs(v[0] - p["ch"]))
            L, R = t[1], t[2]
        if before and after:
            st, en = max(before), min(after)
            how = "PVC/PVT"
        elif L:
            st, en = p["ch"] - L / 2, p["ch"] + L / 2
            how = "PVI +/- L/2"
        else:
            continue
        if L and abs((en - st) - L) > 0.1 and how == "PVC/PVT":
            d.warnings.append(f"vertical curve at PVI {p['ch']:.3f}: PVC..PVT {en - st:.3f} m vs written L={L:g}")
        d.vcurves.append({"R": abs(R) if R else None, "CL": L, "start": round(st, 3), "end": round(en, 3),
                          "vpi": round(p["ch"], 3), "how": how, "partial": False, "src": d.sheet.label})

    # --------------------------------------------------------- horizontal
    pts = []
    for it in its:
        m = _HPT.match(it.text)
        if m:
            v = ch_value(m.group(2))
            if v is not None:
                pts.append((v, m.group(1).upper(), it))
    pts.sort(key=lambda p: p[0])
    band_y = float(np.median([p[2].y for p in pts])) if pts else None
    rtx = []
    for it in its:
        m = _HR.search(it.text)
        if m and band_y is not None and abs(it.y - band_y) < 40 and not _HPT.match(it.text):
            lm = _HL.search(it.text)
            rtx.append((cal(it.x), float(m.group(1)), float(lm.group(1)) if lm else None, it))
    lstx = [(cal(it.x), float(_LS.match(it.text).group(1))) for it in its
            if _LS.match(it.text) and band_y is not None and abs(it.y - band_y) < 40]
    arcs = []
    starts = {"SC", "PC", "BC", "TC", "PCC", "PRC"}
    ends = {"CS", "PT", "EC", "CT", "PCC", "PRC", "SS"}
    for i, (c, k, it) in enumerate(pts):
        if k not in starts and not (k == "SS" and i + 1 < len(pts) and pts[i + 1][1] in ("CS", "SS")):
            continue
        nxt = next(((c2, k2) for c2, k2, _ in pts[i + 1:] if k2 in ends), None)
        if not nxt:
            continue
        sc, cs = c, nxt[0]
        cand = [r for r in rtx if sc - 5 <= r[0] <= cs + 5]
        if not cand:
            cand = [min(rtx, key=lambda r: abs(r[0] - (sc + cs) / 2))] if rtx else []
            if not cand or abs(cand[0][0] - (sc + cs) / 2) > (cs - sc) / 2 + 40:
                d.warnings.append(f"curve {sc:.3f}..{cs:.3f}: no radius text found")
                continue
        r = min(cand, key=lambda r: abs(r[0] - (sc + cs) / 2))
        arcs.append({"R": abs(r[1]), "dir": "left" if r[1] < 0 else "right", "L": r[2], "sc": round(sc, 3),
                     "cs": round(cs, 3), "how": "TS/SC/CS/ST", "partial": False, "src": d.sheet.label})
        d.items_used.append(("horizontal", r[3], f"R={r[1]:g}", sc))
    d.arcs = arcs
    # transitions: TS->SC, CS->ST, CS->SS, SS->SC
    for (c1, k1, _), (c2, k2, _) in zip(pts, pts[1:]):
        if (k1, k2) in (("TS", "SC"), ("CS", "ST"), ("CS", "SS"), ("SS", "SC"), ("TS", "SS"), ("SS", "ST")):
            ls = [v for c, v in lstx if c1 - 5 <= c <= c2 + 5]
            if ls and abs(ls[0] - (c2 - c1)) > 0.1:
                d.warnings.append(f"transition {c1:.3f}..{c2:.3f}: length {c2 - c1:.3f} vs written LS={ls[0]:g}")
            d.transitions.append({"TL": round(c2 - c1, 3), "start": round(c1, 3), "end": round(c2, 3),
                                  "src": d.sheet.label})
    for a in arcs:
        if a["L"] and abs((a["cs"] - a["sc"]) - a["L"]) > 0.1:
            d.warnings.append(f"curve R={a['R']:g}: SC..CS {a['cs'] - a['sc']:.3f} m vs written L={a['L']:g}")

    # curve table -> cant and speed for each arc (matched by radius, in chainage order)
    pref = None
    if s.kind == "main":
        pref = "U" if re.search(r"\bUP\b", s.title, re.I) else "D"
    if tables and arcs:
        pool = [t for t in tables if isinstance(t.get("radius"), float)
                and (pref is None or str(t["no"]).upper().startswith(pref))]
        used = set()
        for a in arcs:
            m = [t for t in pool if id(t) not in used and abs(t["radius"] - a["R"]) <= 0.05]
            if m:
                t = m[0]
                used.add(id(t))
                a["curve_no"] = t["no"]
                if isinstance(t.get("cant"), float):
                    d.cant.append({"ch": (a["sc"] + a["cs"]) / 2, "cant_mm": t["cant"], "src": f"curve table {t['no']}"})
                if isinstance(t.get("vmax"), float):
                    a["vmax"] = t["vmax"]
                    a["vmax_src"] = f"curve table {t['no']}"

    # ------------------------------------------------------------ levels
    nums = [(it, float(_LEVEL.match(it.text).group(1))) for it in its if _LEVEL.match(it.text)]
    if nums and pvis:
        rows: dict[int, list] = {}
        for it, v in nums:
            rows.setdefault(round(it.y), []).append((it, v))
        pc = np.array([p["ch"] for p in pvis]); pz = np.array([p["z"] for p in pvis])
        best = None
        for y, vals in rows.items():
            if len(vals) < 10:
                continue
            diffs = [abs(v - np.interp(cal(it.x), pc, pz)) for it, v in vals]
            score = float(np.median(diffs))
            if best is None or score < best[0]:
                best = (score, vals)
        if best and best[0] < 1.0:
            for it, v in best[1]:
                c = cal(it.x)
                c = round(c / s.step) * s.step if abs(c - round(c / s.step) * s.step) < s.step * 0.3 else round(c, 1)
                d.rail.append((c, v))

    # ---------------------------------------------------------- stations
    for it in its:
        m = _STCH.match(it.text)
        if not m:
            continue
        ch = ch_value(m.group(1))
        names = [o for o in its if o is not it and re.fullmatch(r"[A-Za-z][A-Za-z0-9 .\-/&()']{2,}", o.text.strip())
                 and len(re.sub(r"[^A-Za-z]", "", o.text)) >= 3 and not _NOT_STATION.search(o.text)
                 and not re.fullmatch(r"[A-Za-z]{1,4}\s*-?\s*\d+[A-Za-z]?", o.text.strip())
                 and abs(min(o.w, o.h) - min(it.w, it.h)) <= 0.15 * min(it.w, it.h)
                 and not _TITLE.search(o.text) and abs(o.x - it.x) < 40 and abs(o.y - it.y) < 4 * max(it.h, 1)]
        if ch is None or not names:
            continue
        nm = min(names, key=lambda o: abs(o.y - it.y) + abs(o.x - it.x) * 0.2).text
        nm = re.sub(r"\b(STN\.?|STATION|U/G|E/V)\b", "", nm, flags=re.I).strip(" .-")
        d.stations.append({"name": re.sub(r"\s+", " ", nm.upper()), "ch": ch, "src": d.sheet.label})
    d.ok = bool(d.grades or d.arcs)
    if not d.ok:
        d.warnings.append("no PVI/RL or curve labels found on this profile")
    return d


def interpret_sheet(sheet: Sheet, log=print) -> list[SheetData]:
    strips = find_strips(sheet)
    if not strips:
        return []
    assign(sheet, strips)
    tables = read_curve_tables(sheet)
    if tables:
        log(f"  curve table: {len(tables)} curves (cant / max speed)")
    out = []
    for s in strips:
        d = interpret_strip(sheet, s, tables)
        log(f"  profile '{s.title}': {d.ch_min:.0f}..{d.ch_max:.0f} m, {len(d.grades)} grades, "
            f"{len(d.vcurves)} vertical curves, {len(d.arcs)} curves, {len(d.stations)} stations"
            + ("" if s.kind == "main" else " (not a main line)"))
        out.append(d)
    return out


# ------------------------------------------------------- one route per run
def direction_of(title: str) -> str | None:
    if re.search(r"\bUP\b", title, re.I):
        return "UP"
    if re.search(r"\b(DN|DOWN)\b", title, re.I):
        return "DN"
    return None


def chain_profiles(datas: list[SheetData], direction: str = "UP") -> SheetData | None:
    """Join the main profiles of one direction into a single route, in chainage order.
    A profile that starts inside the previous one is a branch: the route follows the
    previous profile up to the branch start and the branch from there on
    (e.g. UP LINE-1 to 53+000, then UP LINE-2 to Shiv Vihar)."""
    parts = [d for d in datas if d.ok and not d.sheet.is_depot and direction_of(d.sheet.name) == direction]
    if not parts:
        return None
    parts.sort(key=lambda d: (d.ch_min, -d.ch_max))
    chosen = [parts[0]]
    for d in parts[1:]:
        if d.ch_max <= chosen[-1].ch_max + 1 and d.ch_min >= chosen[-1].ch_min:
            if d.ch_min > chosen[-1].ch_min + 1 or d.ch_max > chosen[-1].ch_max:
                chosen.append(d)      # branch that ends inside: still the later part of the route
            continue
        chosen.append(d)
    out = SheetData(chosen[0].sheet)
    out.sheet = Sheet(chosen[0].sheet.source, " + ".join(d.sheet.name for d in chosen),
                      chosen[0].sheet.width, chosen[0].sheet.height)
    bounds = []
    for i, d in enumerate(chosen):
        lo = d.ch_min if i == 0 else chosen[i].ch_min
        hi = chosen[i + 1].ch_min if i + 1 < len(chosen) else d.ch_max
        bounds.append((lo, hi))
    for d, (lo, hi) in zip(chosen, bounds):
        last = d is chosen[-1]
        inside = (lambda c: lo <= c < hi) if not last else (lambda c: lo <= c <= hi)
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
        out.stations += [s for s in d.stations if inside(s["ch"])]
        out.items_used += [u for u in d.items_used if u[3] is None or inside(u[3])]
        out.warnings += [f"{d.sheet.name}: {w}" for w in d.warnings]
    out.ch_min, out.ch_max = bounds[0][0], bounds[-1][1]
    out.info = ["Route joined in chainage order: " + "; ".join(
        f"{d.sheet.name} {lo:.0f}..{hi:.0f} m" for d, (lo, hi) in zip(chosen, bounds))]
    out.ok = True
    return out
