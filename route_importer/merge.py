"""Combine the data of many sheets / drawings into one route, in SEC units."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

from .bands import SheetData
from .units import (cant_from_ramp, curve_speed_limit, equilibrium_cant,
                    fmt_chainage)


@dataclass
class Route:
    name: str
    stations: list = field(default_factory=list)       # dict(name, ch, dwell, src)
    gradients: list = field(default_factory=list)      # dict(from, permil, comment)
    vcurves: list = field(default_factory=list)
    curves: list = field(default_factory=list)         # full horizontal curve records
    cant: list = field(default_factory=list)
    speeds: list = field(default_factory=list)         # dict(from, kmh, comment)
    tunnels: list = field(default_factory=list)
    rail: dict = field(default_factory=dict)           # ch -> level
    ground: dict = field(default_factory=dict)
    start: float = 0.0
    end: float = 0.0
    qa: list = field(default_factory=list)             # dict(check, ch, expected, found, status, note)
    sources: list = field(default_factory=list)
    sheets: list = field(default_factory=list)

    def add_qa(self, check, status, note="", ch=None, expected="", found=""):
        self.qa.append({"check": check, "ch": ch, "expected": expected, "found": found,
                        "status": status, "note": note})


# ------------------------------------------------------------------ stations
_ST = re.compile(r"([A-Z][A-Za-z0-9 .()&'\-/]*?)\s*\bCH\s*[:.]?\s*(-?\d{1,3}\s*\+\s*\d{3}(?:\.\d+)?)")
_NOISE = {"ROOF", "LVL", "LEVEL", "PORTAL", "PIER", "PRO", "PRO.", "GRADIENT", "BSRP", "RAIL", "TRACK",
          "PROPOSED", "EXG", "EXG.", "CROSS", "OVER", "CH", "CL", "CANTILEVER", "FOB", "TO", "AND", "OF",
          "LINE", "TAP", "POINT", "UG", "CABLE", "66KV", "11KV", "HT", "LT", "WATER", "PIPE", "SEWER",
          "DRAIN", "ROAD", "RD", "NALA", "BRIDGE", "FLYOVER", "BUS", "STOP", "BAY", "JN", "TURNOUT",
          "CROSSOVER", "CENTRE", "CENTER", "END", "START"}
_SOFT = {"STATION", "STN", "STN.", "METRO", "(STATION)"}   # dropped without breaking the name


def _clean_name(raw: str) -> str:
    toks = [t for t in raw.replace("\r", " ").split() if t]
    keep = []
    for t in toks:
        u = t.upper()
        if u in _SOFT:
            continue
        if u in _NOISE or u.endswith("-") or "(" in t or ")" in t or len(re.sub(r"[^A-Z]", "", u)) < 2 or re.search(r"\d", t):
            keep = []          # noise breaks the name: keep only what follows
            continue
        keep.append(t)
    return " ".join(keep[-3:]).strip(" .-")


def find_stations(sheets) -> tuple[list, list]:
    found, loose = [], []
    for sh in sheets:
        for line in sh.lines:
            u = line.upper()
            for m in _ST.finditer(u):
                name = _clean_name(m.group(1))
                ch = m.group(2).replace(" ", "")
                km, rest = ch.split("+")
                val = abs(int(km)) * 1000 + float(rest)
                val = -val if km.strip().startswith("-") else val
                if name:
                    found.append({"name": name, "ch": val, "src": sh.label})
            if "STATION" in u and "CH" not in u:
                loose.append((line.strip(), sh.label))
        # name and 'CH: x+xxx' written as separate texts (common in DXF): pair them by position
        for it in sh.items:
            m = re.match(r"^\s*CH\s*[:.]?\s*(-?\d{1,3}\s*\+\s*\d{3}(?:\.\d+)?)\s*$", it.text.upper())
            if not m:
                continue
            size = max(min(it.w, it.h), 1e-6)
            near = [o for o in sh.items if o is not it and re.fullmatch(r"[A-Za-z .&'\-]{3,}", o.text.strip())
                    and abs(o.x - it.x) + abs(o.y - it.y) < 8 * size]
            if near:
                o = min(near, key=lambda o: abs(o.x - it.x) + abs(o.y - it.y))
                name = _clean_name(o.text)
                km, rest = m.group(1).replace(" ", "").split("+")
                val = abs(int(km)) * 1000 + float(rest)
                if name:
                    found.append({"name": name, "ch": -val if km.startswith("-") else val, "src": sh.label})
    found.sort(key=lambda s: s["ch"])
    out = []
    for s in found:
        if out and abs(s["ch"] - out[-1]["ch"]) < 2:
            if len(s["name"]) > len(out[-1]["name"]):
                out[-1]["name"] = s["name"]
            out[-1]["src"] += "; " + s["src"]
            continue
        out.append(dict(s))
    return out, loose


# ------------------------------------------------------------------- helpers
def _dedupe(items, key_fn, tol, prefer=lambda a, b: a):
    items = sorted(items, key=key_fn)
    out = []
    for it in items:
        if out and abs(key_fn(it) - key_fn(out[-1])) <= tol:
            out[-1] = prefer(out[-1], it)
            continue
        out.append(it)
    return out


def _complete_first(a, b):
    pa, pb = a.get("partial", False), b.get("partial", False)
    keep = a if (not pa or pb) else b
    other = b if keep is a else a
    keep.setdefault("src_all", keep["src"])
    if other["src"] not in keep["src_all"]:
        keep["src_all"] += "; " + other["src"]
    return keep


# --------------------------------------------------------------------- build
def build_route(name: str, datas: list[SheetData], sheets, cfg: dict,
                with_stations: bool = True) -> Route:
    r = Route(name)
    r.sheets = sheets
    good = [d for d in datas if d.ok]
    for d in datas:
        for w in d.warnings:
            r.add_qa("Reading", "WARN", f"{d.sheet.label}: {w}")
        for n in getattr(d, "info", []):
            r.add_qa("Route", "INFO", n)
        if not d.ok:
            r.add_qa("Sheet skipped", "WARN", f"{d.sheet.label}: " + "; ".join(d.warnings or d.sheet.notes))
        for row, it, val, ch in d.items_used:
            r.sources.append({"sheet": d.sheet.label, "band": row, "text": it.text, "value": val,
                              "ch": ch, "conf": round(it.conf, 2), "method": it.method})

    # -------------------------------------------------------- coverage
    spans = sorted((d.ch_min, d.ch_max) for d in good)
    cov = []
    for a, b in spans:
        if cov and a <= cov[-1][1] + 20:
            cov[-1][1] = max(cov[-1][1], b)
        else:
            cov.append([a, b])
    for (a1, b1), (a2, b2) in zip(cov, cov[1:]):
        r.add_qa("Coverage", "FAIL" if a2 - b1 > 100 else "WARN", f"no profile data between {fmt_chainage(b1)} and {fmt_chainage(a2)}",
                 b1, found=f"gap {a2 - b1:.0f} m")

    # -------------------------------------------------------- stations
    if with_stations:
        given = [dict(x) for d in datas if d.ok for x in d.stations]
        if given:     # stations read from the profile itself (CAD profiles)
            given.sort(key=lambda x: x["ch"])
            st, loose = [], []
            for x in given:
                if st and abs(x["ch"] - st[-1]["ch"]) < 2:
                    continue
                st.append(x)
        else:
            st, loose = find_stations(sheets)
        for i, s in enumerate(st):
            term = i == 0 or i == len(st) - 1
            s["dwell"] = cfg["terminal_dwell_s"] if term else cfg["dwell_s"]
        r.stations = st
        for line, src in loose:
            r.add_qa("Station without chainage", "WARN",
                     f"'{line[:60]}' on {src}: add manually if it is a stop")
        if len(st) < 2:
            r.add_qa("Stations", "FAIL", "fewer than 2 stations found - add them in the Stations sheet")
        else:
            gaps = np.diff([s["ch"] for s in st])
            r.add_qa("Stations", "OK", f"{len(st)} stations, spacing {gaps.min():.0f}..{gaps.max():.0f} m")
            for s1, s2, g in zip(st, st[1:], gaps):
                if g < 400:
                    r.add_qa("Station spacing", "WARN", f"{s1['name']} -> {s2['name']} only {g:.0f} m apart", s1["ch"])

    allch = [c for c in cov for c in c] + [s["ch"] for s in r.stations]
    r.start, r.end = (min(allch), max(allch)) if allch else (0.0, 0.0)

    # -------------------------------------------------------- levels
    for d in good:
        for ch, v in d.rail:
            k = round(ch, 2)
            if k in r.rail and abs(r.rail[k] - v) > 0.0015:
                r.add_qa("Rail level conflict", "WARN", f"{d.sheet.label}", ch, r.rail[k], v)
            r.rail.setdefault(k, v)
        for ch, v in d.ground:
            r.ground.setdefault(round(ch, 2), v)

    _build_vertical(r, good, cfg)
    _build_horizontal(r, good, cfg)
    _build_speeds(r, good, cfg)
    return r


# ================================================================== vertical
def _build_vertical(r: Route, good, cfg):
    vcs = [dict(v) for d in good for v in d.vcurves]
    vcs = _dedupe(vcs, lambda v: (v["start"] + v["end"]) / 2, 1.0, _complete_first)
    for v in vcs:
        v["vpi"] = round(v.get("vpi") or (v["start"] + v["end"]) / 2, 3)
        v.setdefault("src_all", v["src"])
    obs = [dict(g) for d in good for g in d.grades]
    obs = _dedupe(obs, lambda g: g["ch"], 8.0,
                  lambda a, b: a if a["conf"] >= b["conf"] else b)
    # where the gradient steps from one grade to the next:
    #   vpi = middle of the vertical curve (equal-area step), pvt = end of the curve
    #   (start of the next tangent), pvc = start of the curve
    at = cfg.get("gradient_change_at", "vpi")
    vpis = [v["end"] if at == "pvt" else v["start"] if at == "pvc" else v["vpi"] for v in vcs]

    # break points: VPIs, plus explicit marks where two different grades meet without a curve
    breaks = sorted(vpis)
    for g1, g2 in zip(obs, obs[1:]):
        if abs(g1["permil"] - g2["permil"]) < 1e-6:
            continue
        if any(g1["ch"] < b < g2["ch"] for b in breaks):
            continue
        cands = [x for x in (g1.get("to"), g2.get("from")) if x is not None and g1["ch"] < x < g2["ch"]]
        if cands:
            breaks.append(float(np.mean(cands)))
        else:
            b = (g1["ch"] + g2["ch"]) / 2
            breaks.append(b)
            r.add_qa("Gradient change point", "WARN",
                     f"no chainage mark between {g1['raw']} and {g2['raw']}: change placed mid-way", b)
    breaks = sorted(set(round(b, 3) for b in breaks if r.start < b < r.end))
    edges = [r.start] + breaks + [r.end]
    rail = sorted(r.rail.items())
    segs = []
    for a, b in zip(edges, edges[1:]):
        inside = [g for g in obs if a <= g["ch"] < b]
        if inside:
            vals = {round(g["permil"], 3) for g in inside}
            best = max(inside, key=lambda g: g["conf"])
            cmt = "From drawing"
            if len(vals) > 1:
                r.add_qa("Gradient conflict", "WARN", f"different grades read in {fmt_chainage(a)}..{fmt_chainage(b)}: "
                         + ", ".join(f"{v:g}" for v in sorted(vals)), a)
            segs.append({"from": a, "to": b, "permil": round(best["permil"], 3), "comment": cmt, "obs": best})
        else:
            pts = [(c, z) for c, z in rail if a + 5 <= c <= b - 5 and not any(v["start"] <= c <= v["end"] for v in vcs)]
            if len(pts) >= 2:
                cs, zs = np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
                k = np.polyfit(cs, zs, 1)[0] * 1000
                segs.append({"from": a, "to": b, "permil": round(float(k), 3),
                             "comment": "Calculated from rail levels (no gradient text read)", "obs": None})
                r.add_qa("Gradient", "WARN", f"{fmt_chainage(a)}..{fmt_chainage(b)}: no gradient text, "
                         f"calculated {k:.2f} ‰ from {len(pts)} rail levels", a)
            else:
                prev = segs[-1]["permil"] if segs else 0.0
                segs.append({"from": a, "to": b, "permil": prev,
                             "comment": "ASSUMED - no data, previous gradient carried", "obs": None})
                r.add_qa("Gradient", "FAIL", f"{fmt_chainage(a)}..{fmt_chainage(b)}: no gradient and no rail levels", a)
    # merge equal neighbours
    merged = []
    for s in segs:
        if merged and abs(merged[-1]["permil"] - s["permil"]) < 1e-6 and merged[-1]["comment"] == s["comment"]:
            merged[-1]["to"] = s["to"]
            continue
        merged.append(dict(s))
    r.gradients = merged

    # QA: gradient vs rail levels, tangent length vs marks
    for s in merged:
        pts = [(c, z) for c, z in rail if s["from"] + 5 <= c <= s["to"] - 5
               and not any(v["start"] - 1 <= c <= v["end"] + 1 for v in vcs)]
        if len(pts) >= 3 and s["comment"] == "From drawing":
            cs, zs = np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
            k = float(np.polyfit(cs, zs, 1)[0] * 1000)
            st = "OK" if abs(k - s["permil"]) <= 0.3 else ("WARN" if abs(k - s["permil"]) <= 1.0 else "FAIL")
            r.add_qa("Gradient vs rail levels", st, f"{len(pts)} levels", s["from"], f"{s['permil']:.3f}", f"{k:.3f}")
    for g in obs:
        if g.get("L") and g.get("from") is not None and g.get("to") is not None:
            diff = g["to"] - g["from"]
            st = "OK" if abs(diff - g["L"]) <= 0.05 else "WARN"
            r.add_qa("Tangent length", st, g["raw"], g["from"], f"{g['L']:.3f}", f"{diff:.3f}")

    # vertical curves table with grades in / out
    def grade_at(ch):
        for s in merged:
            if s["from"] <= ch < s["to"]:
                return s["permil"]
        return None
    for v in vcs:
        gi, go = grade_at(v["vpi"] - 0.01), grade_at(v["vpi"] + 0.01)
        typ = "" if gi is None or go is None else ("Crest" if gi > go else ("Sag" if gi < go else "-"))
        r.vcurves.append({"vpi": v["vpi"], "start": v["start"], "end": v["end"], "R": v["R"],
                          "L": round(v["end"] - v["start"], 3), "CL": v.get("CL"), "g_in": gi, "g_out": go,
                          "type": typ, "src": v["src_all"],
                          "comment": "One end off-sheet, estimated" if v.get("partial") else "From drawing"})
        if v.get("CL"):
            L = v["end"] - v["start"]
            r.add_qa("Vertical curve length", "OK" if abs(L - v["CL"]) <= 0.05 else "WARN",
                     f"R={v['R']:g}", v["start"], f"{v['CL']:.3f}", f"{L:.3f}")


# ================================================================ horizontal
def _build_horizontal(r: Route, good, cfg):
    arcs = [dict(a) for d in good for a in d.arcs]
    full = [a for a in arcs if a["sc"] is not None and a["cs"] is not None]
    # partial arcs split over two sheets: join same-radius pieces that touch
    full = _dedupe(full, lambda a: a["sc"], 0.5, _complete_first)
    joined = []
    for a in sorted(full, key=lambda a: a["sc"]):
        if joined and joined[-1]["R"] == a["R"] and a["sc"] <= joined[-1]["cs"] + 0.5 and \
                (a.get("partial") or joined[-1].get("partial")):
            joined[-1]["cs"] = max(joined[-1]["cs"], a["cs"])
            joined[-1]["partial"] = a.get("partial") and joined[-1].get("partial")
            continue
        if joined and a["sc"] < joined[-1]["cs"] - 0.5:
            # OCR dropped/added a digit (R=38 vs R=385): keep the longer reading
            r1, r2 = f"{joined[-1]['R']:g}", f"{a['R']:g}"
            if r1 != r2 and (r1.startswith(r2) or r2.startswith(r1)):
                keep = joined[-1] if len(r1) > len(r2) else a
                r.add_qa("Curve radius misread", "WARN", f"R={r1} and R={r2} read at the same place - kept R={keep['R']:g}", a["sc"])
                joined[-1] = keep
                continue
            # overlap with a different curve -> keep the complete one
            if a.get("partial") and not joined[-1].get("partial"):
                continue
            if joined[-1].get("partial") and not a.get("partial"):
                joined[-1] = a
                continue
            r.add_qa("Curve overlap", "WARN", f"R={joined[-1]['R']:g} and R={a['R']:g} overlap", a["sc"])
        joined.append(a)
    trs = _dedupe([dict(t) for d in good for t in d.transitions if t["start"] is not None and t["end"] is not None],
                  lambda t: t["start"], 0.5)
    cants = [c for d in good for c in d.cant]
    ramp_default = float(cfg["cant_ramp_1_in"])
    G, Cd, Cmax = float(cfg["cant_gauge_mm"]), float(cfg["cant_deficiency_mm"]), float(cfg["max_cant_mm"])
    vline = float(cfg["line_speed_kmh"])
    step = float(cfg["speed_round_down_kmh"]) or 1
    for a in joined:
        tin = next((t for t in trs if abs(t["end"] - a["sc"]) <= 1.0), None)
        tout = next((t for t in trs if abs(t["start"] - a["cs"]) <= 1.0), None)
        ts = tin["start"] if tin else a["sc"]
        st = tout["end"] if tout else a["cs"]
        TLin, TLout = (tin["TL"] if tin else 0.0), (tout["TL"] if tout else 0.0)
        # ---- cant
        drawn = [c for c in cants if ts - 1 <= c["ch"] <= st + 1]
        c_mm = max((c["cant_mm"] for c in drawn if "cant_mm" in c), default=None)
        ramp = next((c["ramp_n"] for c in drawn if "ramp_n" in c), None)
        TL = max(TLin, TLout)
        if c_mm is not None:
            csrc = "From drawing"
            if ramp is None and TL:
                ramp = TL * 1000 / c_mm if c_mm else None
        elif TL:
            n = ramp or ramp_default
            c_mm = min(cant_from_ramp(TL, n), Cmax)
            ramp = n
            csrc = f"Calculated: transition {TL:g} m at 1 in {n:g}" + ("" if c_mm < Cmax else f", capped {Cmax:g} mm")
        else:
            ce = equilibrium_cant(vline, a["R"], G)
            c_mm = min(ce, Cmax)
            ramp = None
            csrc = (f"Calculated: equilibrium cant at {vline:g} km/h (no transition data)"
                    + (f", capped at max {Cmax:g} mm" if ce > Cmax else ""))
        # ---- speed
        v = curve_speed_limit(a["R"], c_mm, Cd, G)
        v_lim = min(vline, math.floor(v / step) * step)
        v_src = "Calculated"
        if a.get("vmax"):
            v_lim, v_src = a["vmax"], f"From drawing ({a.get('vmax_src', '')})"
        ext = cfg["curve_extent_for_sec"]
        if ext == "arc":
            f, t = a["sc"], a["cs"]
        elif ext == "full":
            f, t = ts, st
        else:
            f, t = a["sc"] - TLin / 2, a["cs"] + TLout / 2
        rec = {"ts": ts, "sc": a["sc"], "cs": a["cs"], "st": st, "R": a["R"],
               "arc_L": round(a["cs"] - a["sc"], 3), "L_written": a.get("L"), "TL_in": TLin, "TL_out": TLout,
               "cant_mm": round(c_mm, 1), "ramp_n": round(ramp, 1) if ramp else None, "cant_src": csrc,
               "v_calc": round(v, 1), "v_limit": v_lim, "sec_from": round(f, 3), "sec_to": round(t, 3),
               "src": a.get("src_all", a["src"]), "partial": a.get("partial", False),
               "v_src": v_src, "curve_no": a.get("curve_no", ""), "dir": a.get("dir", "")}
        r.curves.append(rec)
        r.cant.append({"ts": ts, "sc": a["sc"], "cs": a["cs"], "st": st, "R": a["R"], "cant_mm": rec["cant_mm"],
                       "ramp_n": rec["ramp_n"],
                       "ramp_permil": round(1000 / ramp, 3) if ramp else None,
                       "runout_in": TLin, "runout_out": TLout, "comment": csrc})
        if a.get("L"):
            L = a["cs"] - a["sc"]
            r.add_qa("Curve arc length", "OK" if abs(L - a["L"]) <= 0.05 else "WARN",
                     f"R={a['R']:g}", a["sc"], f"{a['L']:.3f}", f"{L:.3f}")
        if a.get("partial"):
            r.add_qa("Curve", "WARN", f"R={a['R']:g}: one end not shown on the sheets read, estimated", a["sc"])
        if a["R"] < 150:
            r.add_qa("Curve radius", "WARN", f"R={a['R']:g} m is very tight for a main line - check (turnout/depot?)", a["sc"])
    r.curves.sort(key=lambda c: c["sc"])
    r.cant.sort(key=lambda c: c["sc"])
    if any("Calculated" in c["comment"] for c in r.cant):
        r.add_qa("Cant", "INFO", f"cant calculated for {sum('Calculated' in c['comment'] for c in r.cant)} curves "
                 f"(ramp 1 in {ramp_default:g}, max {Cmax:g} mm) - confirm with customer if cant table is available")


# ==================================================================== speeds
def _build_speeds(r: Route, good, cfg):
    vline = float(cfg["line_speed_kmh"])
    drawn = sorted([s for d in good for s in d.speed], key=lambda s: s["ch"])
    if drawn:
        pts = [{"from": r.start, "kmh": vline, "comment": "Line speed (setting)"}]
        for s in drawn:
            pts.append({"from": round(s["ch"], 3), "kmh": s["kmh"], "comment": "From drawing"})
        r.speeds = _compress(pts)
        return
    # calculated: line speed everywhere, curve limits over TS..ST
    zones = [(c["ts"], c["st"], c["v_limit"], c) for c in r.curves if c["v_limit"] < vline]
    zones.sort()
    pts = [{"from": round(r.start, 3), "kmh": vline, "comment": "Line speed (setting, no speed data in drawings)"}]
    events = sorted({r.start, r.end} | {z[0] for z in zones} | {z[1] for z in zones})
    for a, b in zip(events, events[1:]):
        mid = (a + b) / 2
        act = [z for z in zones if z[0] <= mid < z[1]]
        if act:
            v = min(z[2] for z in act)
            c = min(act, key=lambda z: z[2])[3]
            if c.get("v_src", "Calculated") != "Calculated":
                cmt = f"{c['v_src']}: R={c['R']:g} m"
            else:
                cmt = f"Calculated: R={c['R']:g} m, cant {c['cant_mm']:g} mm, deficiency {cfg['cant_deficiency_mm']} mm"
        else:
            v, cmt = vline, "Line speed (setting)"
        pts.append({"from": round(a, 3), "kmh": v, "comment": cmt})
    r.speeds = _compress(pts)
    if zones:
        nd = sum(1 for z in zones if z[3].get("v_src", "Calculated") != "Calculated")
        r.add_qa("Speed limits", "INFO", f"{len(zones)} curve speed restrictions: {nd} from the drawing's curve table, "
                 f"{len(zones) - nd} calculated with V = sqrt(127 R (Ca+Cd) / G), G={cfg['cant_gauge_mm']} mm "
                 "(marked 'Calculated')")


def _compress(pts):
    out = []
    for p in sorted(pts, key=lambda p: p["from"]):
        if out and abs(p["from"] - out[-1]["from"]) < 1e-6:
            out[-1] = p
            continue
        if out and p["kmh"] == out[-1]["kmh"]:
            continue
        out.append(p)
    return out
