"""Compare a generated route workbook with a reference route workbook
(e.g. an earlier hand-made SEC route input) over their common chainage range.

  python -m route_importer.compare generated.xlsx reference.xlsx [-o report.xlsx]

Checks stations (by name / order), gradient (‰ along the route), curves
(radius and extent) and speed limits, and writes a report workbook.
"""
from __future__ import annotations

import argparse
import difflib
import re

import numpy as np
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill


def _rows(wb, name):
    if name not in wb.sheetnames:
        return []
    rows = list(wb[name].iter_rows(values_only=True))
    hdr = [str(h).strip().lower() if h else "" for h in rows[0]] if rows else []
    out = []
    for r in rows[1:]:
        if r and any(v is not None for v in r):
            out.append(dict(zip(hdr, r)))
    return out


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def load_route(path):
    wb = load_workbook(path, read_only=True, data_only=True)
    st = [(str(r["name"]), float(r["chainage_m"])) for r in _rows(wb, "Stations") if _num(r.get("chainage_m"))]
    gr = sorted((float(r["from_m"]), float(r["permil"])) for r in _rows(wb, "Gradients")
                if _num(r.get("from_m")) and _num(r.get("permil")))
    cu = sorted((float(r["from_m"]), float(r["to_m"]), float(r["radius_m"])) for r in (_rows(wb, "Horizontal Curves") or _rows(wb, "Curves"))
                if _num(r.get("from_m")) and _num(r.get("radius_m")))
    sp = sorted((float(r["from_m"]), float(r["kmh"])) for r in _rows(wb, "SpeedLimits")
                if _num(r.get("from_m")) and _num(r.get("kmh")))
    tu = sorted((float(r["from_m"]), float(r["to_m"])) for r in _rows(wb, "Tunnels")
                if _num(r.get("from_m")) and _num(r.get("to_m")))
    return {"stations": st, "gradients": gr, "curves": cu, "speeds": sp, "tunnels": tu}


def _step_value(steps, x):
    """Value of a step function [(from, value)] at x."""
    v = None
    for f, val in steps:
        if f <= x:
            v = val
        else:
            break
    return v


def _norm(name):
    n = re.sub(r"[^A-Z]", "", name.upper())
    return n.replace("STATION", "").replace("STN", "")


def compare(gen, ref, lo=None, hi=None, grid=1.0):
    lo = lo if lo is not None else max(min(c for _, c in gen["stations"] or [(0, 0)]) - 500,
                                       min(f for f, _ in ref["gradients"] or [(0, 0)]))
    res = {"range": (lo, hi)}
    # ---------------------------------------------------------- stations
    st_rows = []
    ref_st = [s for s in ref["stations"] if lo <= s[1] <= hi]
    for name, ch in ref_st:
        best = max(gen["stations"], key=lambda g: difflib.SequenceMatcher(None, _norm(name), _norm(g[0])).ratio(),
                   default=None)
        if best and difflib.SequenceMatcher(None, _norm(name), _norm(best[0])).ratio() >= 0.6:
            st_rows.append((name, ch, best[0], best[1], round(best[1] - ch, 3)))
        else:
            st_rows.append((name, ch, "(not found)", None, None))
    res["stations"] = st_rows
    # ---------------------------------------------------------- gradient
    xs = np.arange(lo, hi, grid)
    g_ref = np.array([_step_value(ref["gradients"], x) or 0.0 for x in xs])
    g_gen = np.array([_step_value(gen["gradients"], x) or 0.0 for x in xs])
    diff = np.abs(g_gen - g_ref)
    res["grad_within"] = {t: float((diff <= t).mean() * 100) for t in (0.5, 1.0, 2.0, 5.0)}
    res["grad_rms"] = float(np.sqrt((diff ** 2).mean()))
    # elevation difference that the grade difference builds up (integral) - energy relevant
    z_ref = np.cumsum(g_ref) * grid / 1000
    z_gen = np.cumsum(g_gen) * grid / 1000
    res["elev_max_diff"] = float(np.abs(z_gen - z_ref).max())
    bad = []
    in_bad = False
    for x, d_, a, b in zip(xs, diff, g_ref, g_gen):
        if d_ > 2.0 and not in_bad:
            start, in_bad, ra, gb = x, True, a, b
        elif d_ <= 2.0 and in_bad:
            bad.append((start, x, ra, gb)); in_bad = False
    if in_bad:
        bad.append((start, xs[-1], ra, gb))
    res["grad_bad"] = [b for b in bad if b[1] - b[0] >= 20]
    # ---------------------------------------------------------- curves
    cu_rows = []
    gen_c = [c for c in gen["curves"] if c[1] >= lo and c[0] <= hi]
    used = set()
    for f, t, R in [c for c in ref["curves"] if c[1] >= lo and c[0] <= hi]:
        ov = [(min(t, g[1]) - max(f, g[0]), i, g) for i, g in enumerate(gen_c) if min(t, g[1]) > max(f, g[0]) - 30]
        if ov:
            _, i, g = max(ov)
            used.add(i)
            cu_rows.append((f, t, R, g[0], g[1], g[2], round(g[2] - R, 2), round(g[0] - f, 1), round(g[1] - t, 1)))
        else:
            cu_rows.append((f, t, R, None, None, None, None, None, None))
    extra = [g for i, g in enumerate(gen_c) if i not in used]
    res["curves"] = cu_rows
    res["curves_extra"] = extra
    # curvature (1/R) agreement along the route
    def curv(curves, x):
        return sum(1 / c[2] for c in curves if c[0] <= x < c[1] and c[2])
    k_ref = np.array([curv(ref["curves"], x) for x in xs])
    k_gen = np.array([curv(gen_c, x) for x in xs])
    res["curve_len_ref"] = float((k_ref > 0).sum() * grid)
    res["curve_len_gen"] = float((k_gen > 0).sum() * grid)
    res["curve_work_ratio"] = float(k_gen.sum() / k_ref.sum()) if k_ref.sum() else None
    # ---------------------------------------------------------- tunnels
    def in_t(tl):
        return np.array([any(a <= x < b for a, b in tl) for x in xs]) if tl else np.zeros(len(xs), bool)
    t_ref, t_gen = in_t(ref.get("tunnels")), in_t(gen.get("tunnels"))
    res["tunnel_m"] = (float(t_ref.sum() * grid), float(t_gen.sum() * grid), float((t_ref & t_gen).sum() * grid))
    # ---------------------------------------------------------- speeds
    if ref["speeds"] and gen["speeds"]:
        v_ref = np.array([_step_value(ref["speeds"], x) or 0 for x in xs])
        v_gen = np.array([_step_value(gen["speeds"], x) or 0 for x in xs])
        res["speed_equal"] = float((v_ref == v_gen).mean() * 100)
        res["speed_within5"] = float((np.abs(v_ref - v_gen) <= 5).mean() * 100)
        res["speed_mean_ref"] = float(v_ref.mean())
        res["speed_mean_gen"] = float(v_gen.mean())
    return res


def write_report(res, path, gen_name, ref_name):
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    lo, hi = res["range"]
    st = res["stations"]
    found = [s for s in st if s[3] is not None]
    lines = [
        ("Generated", gen_name), ("Reference", ref_name), ("Compared range (m)", f"{lo:.1f} .. {hi:.1f}"),
        ("Stations found", f"{len(found)} of {len(st)}"),
        ("Station chainage difference (m)", f"mean {np.mean([abs(s[4]) for s in found]):.1f}, max {max(abs(s[4]) for s in found):.1f}"
         if found else "-"),
        ("Gradient within 0.5 / 1 / 2 / 5 permil", " / ".join(f"{res['grad_within'][t]:.1f} %" for t in (0.5, 1.0, 2.0, 5.0))),
        ("Gradient RMS difference (permil)", f"{res['grad_rms']:.2f}"),
        ("Max elevation drift from grade difference (m)", f"{res['elev_max_diff']:.2f}"),
        ("Curves matched", f"{sum(1 for c in res['curves'] if c[3] is not None)} of {len(res['curves'])}; extra in generated: {len(res['curves_extra'])}"),
        ("Length in curves ref / generated (m)", f"{res['curve_len_ref']:.0f} / {res['curve_len_gen']:.0f}"),
        ("Curve resistance work generated / ref (sum 1/R)", f"{res['curve_work_ratio']:.3f}" if res["curve_work_ratio"] else "-"),
    ]
    lines.append(("Tunnel length ref / generated / both (m)", "%.0f / %.0f / %.0f" % res["tunnel_m"]))
    if "speed_equal" in res:
        lines += [("Speed limit equal / within 5 km/h", f"{res['speed_equal']:.1f} % / {res['speed_within5']:.1f} %"),
                  ("Mean speed limit ref / generated", f"{res['speed_mean_ref']:.1f} / {res['speed_mean_gen']:.1f} km/h")]
    for a, b in lines:
        ws.append([a, b])
    ws.column_dimensions["A"].width = 48
    ws.column_dimensions["B"].width = 70
    for c in ws["A"]:
        c.font = Font(bold=True)

    def sheet(title, hdr, rows):
        w = wb.create_sheet(title)
        w.append(hdr)
        for c in w[1]:
            c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="1F4E78")
        for r in rows:
            w.append(list(r))
        for i in range(1, len(hdr) + 1):
            w.column_dimensions[chr(64 + i)].width = 16
        return w
    sheet("Stations", ["ref_name", "ref_ch", "gen_name", "gen_ch", "diff_m"], st)
    sheet("Gradient_diffs", ["from_m", "to_m", "ref_permil", "gen_permil"], res["grad_bad"])
    sheet("Curves", ["ref_from", "ref_to", "ref_R", "gen_from", "gen_to", "gen_R", "R_diff", "from_diff", "to_diff"],
          res["curves"])
    sheet("Curves_extra", ["gen_from", "gen_to", "gen_R"], res["curves_extra"])
    wb.save(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("generated"); ap.add_argument("reference")
    ap.add_argument("-o", "--out", default="route_comparison.xlsx")
    ap.add_argument("--from_m", type=float); ap.add_argument("--to_m", type=float)
    a = ap.parse_args()
    gen, ref = load_route(a.generated), load_route(a.reference)
    lo = a.from_m if a.from_m is not None else max(gen["gradients"][0][0], ref["gradients"][0][0])
    hi = a.to_m if a.to_m is not None else min(gen["gradients"][-1][0], ref["gradients"][-1][0])
    res = compare(gen, ref, lo, hi)
    write_report(res, a.out, a.generated, a.reference)
    print(f"report written: {a.out}")


if __name__ == "__main__":
    main()
