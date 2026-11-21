"""LandXML alignment files (e.g. Bentley OpenRail / Civil 3D export) -> SEC route.

A LandXML alignment file holds the exact design data, so nothing has to be read
from a drawing:
  CoordGeom  : Line / Spiral / Curve elements          -> horizontal curves, transitions
  Profile    : PVI + ParaCurve (vertical curve length) -> gradients, vertical curves, levels
  Cant       : CantStation appliedCant                 -> cant
  SpeedStation                                         -> speed limits
  '<CODE> STATION' alignments (short lines)            -> station stop points (projected on the track)

Every long alignment with geometry becomes one route: 'MAINLINE' tracks are main
outputs, the others (CMC / depot connections) separate outputs like depot sheets.
Crossovers and short pieces (shafts etc.) are listed in QA only.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET

from . import units
from .merge import Route, apply_dwells


# ------------------------------------------------------------------ parsing
def _pt(el):
    """LandXML points are 'northing easting [z]' -> (x=east, y=north)."""
    if el is None or not (el.text or "").strip():
        return None
    v = [float(x) for x in el.text.split()]
    return (v[1], v[0])


def _r(v):
    if v is None or str(v).upper() in ("INF", "INFINITY", ""):
        return math.inf
    return float(v)


def parse(path: str) -> list[dict]:
    root = ET.parse(path).getroot()
    ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
    q = lambda t: ns + t
    out = []
    for a in root.iter(q("Alignment")):
        al = {"name": (a.get("name") or "").strip(), "sta0": float(a.get("staStart") or 0),
              "length": float(a.get("length") or 0), "elems": [], "pvis": [], "cant": [], "speed": []}
        cg = a.find(q("CoordGeom"))
        if cg is not None:
            for e in cg:
                tag = e.tag.replace(ns, "")
                if tag not in ("Line", "Curve", "Spiral"):
                    continue
                d = {"type": tag, "L": float(e.get("length") or 0), "rot": e.get("rot"),
                     "S": _pt(e.find(q("Start"))), "E": _pt(e.find(q("End"))),
                     "C": _pt(e.find(q("Center"))), "PI": _pt(e.find(q("PI")))}
                if tag == "Curve":
                    d["R"] = float(e.get("radius"))
                elif tag == "Spiral":
                    d["R1"], d["R2"] = _r(e.get("radiusStart")), _r(e.get("radiusEnd"))
                if tag == "Line" and not d["L"] and d["S"] and d["E"]:
                    d["L"] = math.dist(d["S"], d["E"])
                al["elems"].append(d)
        pa = a.find(q("Profile") + "/" + q("ProfAlign"))
        if pa is not None:
            al["profile_name"] = pa.get("name")
            for e in pa:
                tag = e.tag.replace(ns, "")
                if tag in ("PVI", "ParaCurve", "CircCurve", "UnsymParaCurve") and (e.text or "").strip():
                    s, z = [float(x) for x in e.text.split()[:2]]
                    L = float(e.get("length") or 0) if tag != "UnsymParaCurve" else \
                        float(e.get("lengthIn") or 0) + float(e.get("lengthOut") or 0)
                    al["pvis"].append((s, z, L))
        for c in a.iter(q("CantStation")):
            al["cant"].append((float(c.get("station")), float(c.get("appliedCant") or 0)))
        for s in a.iter(q("SpeedStation")):
            al["speed"].append((float(s.get("station")), float(s.get("speed"))))
        out.append(al)
    return out


# ---------------------------------------------------------------- geometry
def sample(al: dict, step: float = 1.0) -> list[tuple[float, float, float]]:
    """(chainage, x, y) along the alignment, about every `step` m."""
    pts, ch = [], al["sta0"]
    for e in al["elems"]:
        L = e["L"]
        n = max(1, int(math.ceil(L / step)))
        S, E = e["S"], e["E"]
        for i in range(n + (1 if e is al["elems"][-1] else 0)):
            t = min(1.0, i / n)
            if e["type"] == "Curve" and e["C"]:
                C = e["C"]
                a0 = math.atan2(S[1] - C[1], S[0] - C[0])
                sg = -1.0 if e["rot"] == "cw" else 1.0
                ang = a0 + sg * t * L / e["R"]
                x, y = C[0] + e["R"] * math.cos(ang), C[1] + e["R"] * math.sin(ang)
            elif e["type"] == "Spiral" and e["PI"]:
                x, y = _clothoid(e, t * L)
            else:
                x, y = S[0] + t * (E[0] - S[0]), S[1] + t * (E[1] - S[1])
            pts.append((ch + t * L, x, y))
        ch += L
    return pts


def _clothoid(e, s_len):
    """Point at distance s along a clothoid: curvature changes linearly from 1/R1 to 1/R2,
    starting at Start in the direction of the PI."""
    S, P, L = e["S"], e["PI"], e["L"]
    sg = -1.0 if e["rot"] == "cw" else 1.0
    k1 = 0.0 if math.isinf(e["R1"]) else sg / e["R1"]
    k2 = 0.0 if math.isinf(e["R2"]) else sg / e["R2"]
    th0 = math.atan2(P[1] - S[1], P[0] - S[0])
    n = max(4, int(s_len / 0.25))
    h = s_len / n if n else 0
    x, y = S
    th = lambda u: th0 + k1 * u + (k2 - k1) * u * u / (2 * L)
    for i in range(n):                    # Simpson on each step
        u0, u1 = i * h, (i + 1) * h
        um = (u0 + u1) / 2
        x += h / 6 * (math.cos(th(u0)) + 4 * math.cos(th(um)) + math.cos(th(u1)))
        y += h / 6 * (math.sin(th(u0)) + 4 * math.sin(th(um)) + math.sin(th(u1)))
    return x, y


def project(pts, x, y):
    """Chainage and distance of the nearest point of the sampled alignment."""
    best = min(range(len(pts)), key=lambda i: (pts[i][1] - x) ** 2 + (pts[i][2] - y) ** 2)
    lo, hi = max(0, best - 1), min(len(pts) - 1, best + 1)
    # refine on the two neighbouring segments
    cands = []
    for i, j in ((lo, best), (best, hi)):
        if i == j:
            cands.append((math.hypot(pts[i][1] - x, pts[i][2] - y), pts[i][0])); continue
        ax, ay, bx, by = pts[i][1], pts[i][2], pts[j][1], pts[j][2]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy or 1e-12
        t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / L2))
        cands.append((math.hypot(ax + t * dx - x, ay + t * dy - y), pts[i][0] + t * (pts[j][0] - pts[i][0])))
    d, ch = min(cands)
    return ch, d


def h_curves(al: dict) -> list[dict]:
    """Curve elements with their transitions: TS, SC, CS, ST, R, lengths, direction."""
    out, ch = [], al["sta0"]
    starts = []
    for e in al["elems"]:
        starts.append(ch); ch += e["L"]
    els = al["elems"]
    for i, e in enumerate(els):
        if e["type"] != "Curve":
            continue
        sc, cs = starts[i], starts[i] + e["L"]
        ts, st, tin, tout = sc, cs, 0.0, 0.0
        if i > 0 and els[i - 1]["type"] == "Spiral" and abs(els[i - 1]["R2"] - e["R"]) < 0.5:
            p = els[i - 1]
            tin = p["L"] if math.isinf(p["R1"]) else p["L"] / 2      # compound: share the spiral
            ts = sc - tin
        if i + 1 < len(els) and els[i + 1]["type"] == "Spiral" and abs(els[i + 1]["R1"] - e["R"]) < 0.5:
            p = els[i + 1]
            tout = p["L"] if math.isinf(p["R2"]) else p["L"] / 2
            st = cs + tout
        out.append({"ts": ts, "sc": sc, "cs": cs, "st": st, "R": round(e["R"], 3), "arc_L": round(e["L"], 3),
                    "TL_in": round(tin, 3), "TL_out": round(tout, 3),
                    "dir": "Right" if e["rot"] == "cw" else "Left"})
    return out


def z_at(pvis, s):
    """Level on the vertical alignment (parabolic vertical curves)."""
    if not pvis:
        return None
    if s <= pvis[0][0]:
        i = 0
    elif s >= pvis[-1][0]:
        i = len(pvis) - 2
    else:
        i = max(k for k in range(len(pvis) - 1) if pvis[k][0] <= s)
    g = lambda k: (pvis[k + 1][1] - pvis[k][1]) / (pvis[k + 1][0] - pvis[k][0])
    for k in range(1, len(pvis) - 1):           # inside a vertical curve?
        sv, zv, L = pvis[k]
        if L and sv - L / 2 <= s <= sv + L / 2:
            g1, g2 = g(k - 1), g(k)
            x = s - (sv - L / 2)
            return zv - g1 * L / 2 + g1 * x + (g2 - g1) * x * x / (2 * L)
    return pvis[i][1] + g(i) * (s - pvis[i][0]) if len(pvis) > 1 else pvis[0][1]


# ------------------------------------------------------------------- route
_STATION = re.compile(r"^\s*(.+?)\s+STATION\s*$", re.I)


def station_points(aligns):
    """Stop point of each '<CODE> STATION' alignment (its middle)."""
    out = []
    for a in aligns:
        m = _STATION.match(a["name"])
        if not m or not a["elems"]:
            continue
        pts = sample(a, 0.5)
        mid = a["sta0"] + sum(e["L"] for e in a["elems"]) / 2
        i = max(k for k in range(len(pts) - 1) if pts[k][0] <= mid) if pts[0][0] <= mid else 0
        j = min(i + 1, len(pts) - 1)
        t = 0.0 if j == i else (mid - pts[i][0]) / (pts[j][0] - pts[i][0])
        out.append({"name": m.group(1).strip().upper(),
                    "x": pts[i][1] + t * (pts[j][1] - pts[i][1]), "y": pts[i][2] + t * (pts[j][2] - pts[i][2]),
                    "z": z_at(a["pvis"], mid)})
    return out


def is_track(a):
    return len(a["elems"]) > 0 and a["length"] >= 300 and not re.search(r"CROSSOVER|STATION", a["name"], re.I)


def track_label(a):
    n = re.sub(r"\s*-\s*HORIZONTAL ALIGNMENT\s*$", "", a["name"], flags=re.I).strip()
    n = re.sub(r"^\s*M\d+\s+", "", n)            # 'M5 MAINLINE TRACK 1' -> 'MAINLINE TRACK 1'
    return n.title()


def build(a: dict, stations: list, cfg: dict, src: str) -> Route:
    name = track_label(a)
    r = Route(name)
    r.start, r.end = a["sta0"], a["sta0"] + sum(e["L"] for e in a["elems"])
    pts = sample(a, 1.0)
    # ---------------------------------------------------------- stations
    st = []
    for s in stations:
        ch, d = project(pts, s["x"], s["y"])
        if d <= 15 and r.start - 1 <= ch <= r.end + 1:
            st.append({"name": s["name"], "ch": round(ch, 3), "offset": d})
    st.sort(key=lambda s: s["ch"])
    for i, s in enumerate(st):
        term = i == 0 or i == len(st) - 1
        s["dwell"] = cfg.get("terminal_dwell_s") if term else cfg.get("dwell_s")
        s["src"] = f"{src}: {s['name']} STATION alignment, projected {s['offset']:.1f} m onto the track"
    apply_dwells(st, cfg, r)
    r.stations = st
    if st:
        r.add_qa("Stations", "INFO", f"{len(st)} stations from the '<code> STATION' alignments, stop point = "
                 "middle of the station alignment projected on this track")
    # ---------------------------------------------------------- vertical
    pv = sorted(a["pvis"])
    at = cfg.get("gradient_change_at", "pvt")
    grads = []
    for k in range(len(pv) - 1):
        g = (pv[k + 1][1] - pv[k][1]) / (pv[k + 1][0] - pv[k][0]) * 1000
        s0 = pv[k][0]
        if k > 0 and pv[k][2]:
            s0 += {"pvt": pv[k][2] / 2, "pvc": -pv[k][2] / 2}.get(at, 0)
        grads.append({"from": round(max(s0, r.start), 3), "permil": round(g, 3), "comment": "From LandXML"})
    r.gradients = grads
    vcs = []
    for k in range(1, len(pv) - 1):
        sv, zv, L = pv[k]
        g1 = (pv[k][1] - pv[k - 1][1]) / (pv[k][0] - pv[k - 1][0]) * 1000
        g2 = (pv[k + 1][1] - pv[k][1]) / (pv[k + 1][0] - pv[k][0]) * 1000
        if not L:
            continue
        dg = g2 - g1
        vcs.append({"vpi": sv, "start": sv - L / 2, "end": sv + L / 2,
                    "R": round(L / abs(dg) * 1000, 1) if abs(dg) > 1e-6 else None, "L": L,
                    "g_in": round(g1, 3), "g_out": round(g2, 3),
                    "type": "Crest" if dg < 0 else "Sag" if dg > 0 else "-", "comment": "From LandXML", "src": src})
    r.vcurves = vcs
    if pv:
        c = r.start
        while c <= r.end + 1e-6:
            r.rail[round(c, 3)] = round(z_at(pv, c), 3)
            c += 10.0
    # ---------------------------------------------------------- speeds
    sp = sorted(set(a["speed"]))
    speeds = []
    for s, v in sp:
        v = round(v, 1)
        if speeds and speeds[-1]["kmh"] == v:
            continue
        speeds.append({"from": round(max(s, r.start), 3), "kmh": int(v) if v == int(v) else v,
                       "comment": "From LandXML (speed station)"})
    if not speeds:
        speeds = [{"from": r.start, "kmh": cfg["line_speed_kmh"], "comment": "Line speed (setting) - no speed stations"}]
        r.add_qa("Speed limits", "WARN", "no SpeedStation data in the LandXML - line speed used everywhere")
    r.speeds = speeds

    def vmin(f, t):
        vs = [v for s, v in sp if f <= s < t]
        before = [v for s, v in sp if s < f]
        if before:
            vs.append(before[-1])
        return min(vs) if vs else None
    # ---------------------------------------------------------- horizontal + cant
    ca = sorted(set(a["cant"]))
    ext = cfg["curve_extent_for_sec"]
    gauge, cd, ramp = float(cfg["cant_gauge_mm"]), float(cfg["cant_deficiency_mm"]), float(cfg["cant_ramp_1_in"])
    n_calc, implied = 0, []
    for i, c in enumerate(h_curves(a), 1):
        f, t = {"arc": (c["sc"], c["cs"]), "full": (c["ts"], c["st"])}.get(
            ext, (c["sc"] - c["TL_in"] / 2, c["cs"] + c["TL_out"] / 2))
        applied = [v for s, v in ca if c["ts"] - 0.5 <= s <= c["st"] + 0.5]
        if applied and max(applied) > 0:
            cant, how = round(max(applied), 1), "From LandXML (cant station)"
        else:
            tl = max(c["TL_in"], c["TL_out"])
            cant = round(min(units.cant_from_ramp(tl, ramp), float(cfg["max_cant_mm"])), 1) if tl else 0.0
            how = (f"Calculated: no cant in the LandXML - transition {tl:g} m at 1 in {ramp:g}" if tl
                   else "No cant in the LandXML and no transition - 0")
            n_calc += 1
        vc = units.curve_speed_limit(c["R"], cant, cd, gauge)
        vl = vmin(c["ts"], c["st"])
        c.update(cant_mm=cant, v_calc=round(vc, 1), v_limit=vl, v_src="LandXML speed stations",
                 sec_from=round(f, 3), sec_to=round(t, 3), curve_no=f"HC{i}", src=src, partial=False)
        if vl:
            implied.append((units.equilibrium_cant(vl, c["R"], gauge) - cant, c))
        r.curves.append(c)
        tin, tout = c["TL_in"], c["TL_out"]
        r.cant.append({"ts": c["ts"], "sc": c["sc"], "cs": c["cs"], "st": c["st"], "R": c["R"], "cant_mm": cant,
                       "ramp_n": round(max(tin, tout) / cant * 1000) if cant and max(tin, tout) else None,
                       "ramp_permil": round(cant / max(tin, tout), 2) if cant and max(tin, tout) else None,
                       "runout_in": tin, "runout_out": tout, "comment": how})
    if implied:
        mx, cm = max(implied, key=lambda x: x[0])
        r.add_qa("Curve speed", "INFO" if mx <= cd + 0.5 else "WARN",
                 f"the speed stations use cant deficiency up to {mx:.0f} mm (R={cm['R']:g} m at {cm['v_limit']:g} km/h, "
                 f"cant {cm['cant_mm']:g} mm); settings Cd = {cd:g} mm" +
                 ("" if mx <= cd + 0.5 else " - the design allows more; speeds are taken from the LandXML as they are"),
                 cm["sc"])
    if n_calc:
        r.add_qa("Cant", "WARN", f"{n_calc} of {len(r.curves)} curves have no applied cant in the LandXML - "
                 f"calculated from the transition length at 1 in {ramp:g} (information only: the speed "
                 "limits come from the speed stations)")
    # ---------------------------------------------------------- QA / sources
    r.add_qa("Tunnels", "WARN", "LandXML alignments carry no tunnel / viaduct data - enter underground "
             "sections in the Tunnels sheet if the line has any")
    r.add_qa("Source", "INFO", f"{src}: {len(a['elems'])} geometry elements, {len(pv)} PVIs, "
             f"{len(sp)} speed stations, {len(ca)} cant stations; chainage {units.fmt_chainage(r.start)} to "
             f"{units.fmt_chainage(r.end)}")
    for s, z, L in pv:
        r.sources.append({"sheet": src, "band": "Profile", "text": f"PVI {s:.3f} {z:.3f}" + (f" L={L:g}" if L else ""),
                          "value": z, "ch": s, "conf": 1.0, "method": "LandXML"})
    r.readme = [
        f"{name} - ROUTE input for the SEC simulator, from the LandXML alignment file",
        "",
        f"SOURCE: {src}",
        f"  alignment '{a['name']}', chainage {units.fmt_chainage(r.start)} to {units.fmt_chainage(r.end)} "
        f"({r.end - r.start:.0f} m)",
        "",
        "How values were made",
        "  Stations: '<CODE> STATION' alignments; stop point = middle of the station alignment projected on this track.",
        f"  Dwell: per station from the route list ('CODE dwell=17'), else {cfg.get('dwell_s')} s / terminals "
        f"{cfg.get('terminal_dwell_s')} s (settings; blank = left empty).",
        f"  Gradient (permil, + = rising with increasing chainage) from the PVIs; change point at each vertical "
        f"curve: {at.upper()}.",
        f"  Horizontal Curves: Curve elements with their spirals, extent '{ext}' (full = TS to ST).",
        "  SpeedLimits: the speed stations of the LandXML.",
        "  Cant: applied cant of the cant stations; where the file has none, transition length / ramp (marked Calculated).",
        "  Tunnels: not in LandXML - enter manually.",
        "",
        "Sheets: Stations, Gradients, Horizontal Curves, Tunnels, SpeedLimits = SEC simulator input;",
        "  VerticalCurves, Cant, *_Details, Rail_Levels (track level every 10 m), QA, Sources = information.",
        "",
        "Import: Route tab -> Import Excel / CSV -> this file.",
    ]
    return r


def routes(path: str, cfg: dict, log=print):
    """[(label, Route, is_main)] for every track alignment in the file."""
    import os
    src = os.path.basename(path)
    aligns = parse(path)
    stations = station_points(aligns)
    skipped = sorted({a["name"] for a in aligns if not is_track(a) and a["elems"] and not _STATION.match(a["name"])})
    out = []
    for a in aligns:
        if not is_track(a):
            continue
        r = build(a, stations, cfg, src)
        main = bool(re.search(r"MAIN", a["name"], re.I))
        if skipped:
            r.add_qa("Not used", "INFO", "short alignments (crossovers, shafts ...): " + ", ".join(skipped)[:900])
        log(f"  {r.name}: {len(r.stations)} stations, {len(r.gradients)} gradients, {len(r.curves)} curves, "
            f"{len(r.speeds)} speed limits")
        out.append((re.sub(r"[^A-Za-z0-9]+", "_", r.name).strip("_"), r, main))
    return out
