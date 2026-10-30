"""Interpret the profile-band table of one sheet.

Steps
1. name each band row from its label (VERTICAL, HORIZONTAL, RAIL LEVEL, ...)
2. calibrate x -> chainage from the CHAINAGE row (outlier-robust, piecewise)
3. read each row into events with chainages:
   vertical   : grade texts, vertical curves (R + CL between two chainage marks)
   horizontal : circular arcs (R + L), transitions (TL), tangents
   rail/ground: levels every grid step
   cant/speed : values if the drawing has such rows
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from .sources import Item, Sheet
from .units import (clean_num_text, flip_text, parse_cant, parse_gradient,
                    parse_tagged)

ROW_KEYWORDS = [   # order matters (first match wins); OCR-tolerant word stems
    ("other", ["LEVEL DIFF", "DIFF", "ROAD AND", "ROAD & RAIL"]),
    ("vertical", ["VERTIC", "GRADIENT", "L-SECTION", "PROFILE", "GRADE"]),
    ("horizontal", ["HORIZ", "CURVATURE"]),
    ("cant", ["CANT", "SUPER ELEV", "SUPERELEV", "S.E"]),
    ("speed", ["SPEED"]),
    ("rail", ["RAIL LEV", "RAIL LVL", "FRL", "TOP OF RAIL", "T.O.R", "TRACK LEV", "FORMATION", "PROPOSED LEV"]),
    ("ground", ["GROUND", "EXISTING", "NGL", "EGL", "OGL"]),
    ("chainage", ["CHAIN", "CHAINAG", "CHAINAQ", "CH.", "KM"]),
]

_CH_RE = re.compile(r"(-?\d{1,3})\s*\+\s*(\d{3})(?:\s*[. ]\s*(\d{1,3}))?")
_LEVEL_RE = re.compile(r"\d{1,4}\.\d{2,3}")


def find_chainages(text: str) -> list[float]:
    """All km+m chainages in a (possibly noisy, possibly upside-down) text."""
    def scan(s):
        out = []
        for m in _CH_RE.finditer(s):
            km, mm, dec = m.groups()
            v = abs(int(km)) * 1000 + int(mm) + (float("0." + dec) if dec else 0.0)
            out.append(-v if km.startswith("-") else v)
        return out
    s = text.translate(str.maketrans({"O": "0", "o": "0", ",": ".", "'": ".", "：": ":",
                                      "−": "-", "＋": "+", "l": "1", "I": "1"}))
    found = scan(s)
    if found:
        return found
    core = re.sub(r"[^0-9+.'\-]", "", text)
    fl = flip_text(core) if core else None
    return scan(fl) if fl else []


@dataclass
class Mark:            # a chainage written in a band (x position + value)
    x: float
    ch: float
    conf: float
    text: str


@dataclass
class SheetData:
    sheet: Sheet
    rows: dict = field(default_factory=dict)            # name -> (y0, y1)
    ch_min: float | None = None
    ch_max: float | None = None
    grades: list = field(default_factory=list)          # dict(ch, permil, L, conf, raw)
    vcurves: list = field(default_factory=list)         # dict(start, end, R, CL)
    v_marks: list = field(default_factory=list)         # explicit chainages in vertical row
    arcs: list = field(default_factory=list)            # dict(sc, cs, R, L, partial)
    transitions: list = field(default_factory=list)     # dict(start, end, TL)
    tangents_h: list = field(default_factory=list)
    rail: list = field(default_factory=list)            # (ch, level)
    ground: list = field(default_factory=list)
    cant: list = field(default_factory=list)            # dict(ch, cant_mm | ramp_n)
    speed: list = field(default_factory=list)           # dict(ch, kmh)
    stations: list = field(default_factory=list)        # dict(name, ch, src) when the reader finds them
    items_used: list = field(default_factory=list)      # for the Sources sheet
    warnings: list = field(default_factory=list)
    ok: bool = False


# ---------------------------------------------------------------- calibration
class Calib:
    """x -> chainage, piecewise linear through consistent chainage marks."""

    def __init__(self, marks: list[Mark]):
        self.marks = sorted(marks, key=lambda m: m.x)
        self.bad: list[Mark] = []
        self._fit()

    def _fit(self):
        m = self.marks
        if len(m) < 3:
            self.xs = np.array([k.x for k in m]); self.cs = np.array([k.ch for k in m])
            return
        x = np.array([k.x for k in m]); c = np.array([k.ch for k in m])
        keep = np.ones(len(m), bool)
        for _ in range(3):
            idx = np.where(keep)[0]
            for i in idx:
                nb = [j for j in idx if j != i and abs(j - i) <= 5]
                if len(nb) < 2:
                    continue
                slopes = [(c[j] - c[k]) / (x[j] - x[k]) for a, j in enumerate(nb) for k in nb[a + 1:]
                          if x[j] != x[k]]
                if not slopes:
                    continue
                s = float(np.median(slopes))
                icpt = float(np.median([c[j] - s * x[j] for j in nb]))
                step = abs(s) * float(np.median(np.diff(np.sort(x[nb])))) if len(nb) > 1 else 1
                if abs(c[i] - (icpt + s * x[i])) > max(0.5, 0.3 * step):
                    keep[i] = False
        self.bad = [k for k, ok in zip(m, keep) if not ok]
        self.xs, self.cs = x[keep], c[keep]

    @property
    def ok(self):
        return len(self.xs) >= 2

    def __call__(self, x: float) -> float:
        xs, cs = self.xs, self.cs
        if x <= xs[0]:
            s = (cs[1] - cs[0]) / (xs[1] - xs[0])
            return float(cs[0] + s * (x - xs[0]))
        if x >= xs[-1]:
            s = (cs[-1] - cs[-2]) / (xs[-1] - xs[-2])
            return float(cs[-1] + s * (x - xs[-1]))
        return float(np.interp(x, xs, cs))


# -------------------------------------------------------------------- helpers
def _row_of(y, rows):
    for name, (a, b) in rows.items():
        if a <= y < b:
            return name
    return None


def name_rows(sheet: Sheet, extra_keywords: dict | None = None) -> dict:
    """Name band rows from their labels (left label column only - legends and notes
    beside the band are ignored); unlabeled rows are recognised by their content."""
    kw = [(n, list(k) + [w.upper() for w in (extra_keywords or {}).get(n, [])]) for n, k in ROW_KEYWORDS]
    nums = sorted(it.x for it in sheet.band_items if re.search(r"\d", it.text))
    x_lab = nums[max(0, int(len(nums) * 0.03))] if nums else sheet.width * 0.15
    named, unnamed = {}, []
    for y0, y1 in sheet.band_rows:
        labels = [it for it in sheet.band_items if y0 <= it.y < y1 and it.x < x_lab
                  and not re.search(r"\d", it.text) and len(re.sub(r"[^A-Z]", "", it.text.upper())) >= 3]
        text = " ".join(it.text.upper() for it in sorted(labels, key=lambda i: (i.y, i.x)))
        hit = None
        for name, words in kw:
            if any(w in text for w in words):
                hit = name
                break
        if hit and hit not in named:
            named[hit] = (y0, y1)
        elif not hit:
            unnamed.append((y0, y1))
    # content fallback
    for y0, y1 in unnamed:
        its = [it for it in sheet.band_items if y0 <= it.y < y1]
        if not its:
            continue
        n_ch = sum(1 for it in its if find_chainages(it.text))
        n_lv = sum(1 for it in its if _LEVEL_RE.search(it.text) and not find_chainages(it.text))
        n_tag = sum(1 for it in its if parse_tagged(it.text))
        n_gr = sum(1 for it in its if "%" in it.text or "‰" in it.text)
        if "chainage" not in named and n_ch >= 6 and n_ch >= 0.5 * len(its):
            named["chainage"] = (y0, y1)
        elif "vertical" not in named and n_gr >= 1:
            named["vertical"] = (y0, y1)
        elif "rail" not in named and n_lv >= 8 and n_lv >= 0.6 * len(its):
            named["rail"] = (y0, y1)
    return named


def _marks_in(items) -> list[Mark]:
    out = []
    for it in items:
        for v in find_chainages(it.text)[:1]:
            out.append(Mark(it.x, v, it.conf, it.text))
    return out


def _levels_in(items, cal, prefer=None):
    vals = []
    for it in items:
        nums = [float(n) for n in _LEVEL_RE.findall(clean_num_text(it.text))]
        if not nums:
            core = re.sub(r"[^0-9.'\-]", "", it.text)
            fl = flip_text(core) if core else None
            nums = [float(n) for n in _LEVEL_RE.findall(fl or "")]
        if nums:
            vals.append((it, nums))
    if not vals:
        return []
    med = prefer if prefer is not None else float(np.median([n[0] for _, n in vals]))
    out = []
    for it, nums in vals:
        v = min(nums, key=lambda n: abs(n - med))
        if abs(v - med) < 60:
            out.append((round(cal(it.x), 3), v, it))
    # drop OCR misreads: a level must sit near the levels around it (max ~4 % slope)
    out.sort(key=lambda t: t[0])
    good = []
    for i, (c, v, it) in enumerate(out):
        nb = [v2 for c2, v2, _ in out[max(0, i - 4):i + 5] if c2 != c and abs(c2 - c) <= 60]
        if len(nb) >= 2 and abs(v - float(np.median(nb))) > 0.5 + 0.04 * 30:
            continue
        good.append((c, v, it))
    return good


def _reconcile(obj, a, b, lkey, d, what):
    """start/end marks vs written length: repair one truncated mark (OCR lost the
    decimals) when the other mark + length agree; otherwise warn."""
    s, e, L = obj.get(a), obj.get(b), obj.get(lkey)
    if s is None or e is None or not L:
        return
    if abs((e - s) - L) <= 0.05:
        return
    s_int, e_int = abs(s - round(s)) < 1e-9, abs(e - round(e)) < 1e-9
    if s_int and not e_int:
        obj[a] = round(e - L, 3)
        d.warnings.append(f"{what}: start mark read as {s:g}, corrected to {obj[a]:.3f} from length {L:g}")
    elif e_int and not s_int:
        obj[b] = round(s + L, 3)
        d.warnings.append(f"{what}: end mark read as {e:g}, corrected to {obj[b]:.3f} from length {L:g}")
    else:
        d.warnings.append(f"{what}: marks {s:.3f}..{e:.3f} ({e - s:.3f} m) disagree with written length {L:g} m")


# ----------------------------------------------------------------- main entry
def interpret(sheet: Sheet, extra_keywords: dict | None = None) -> SheetData:
    d = SheetData(sheet)
    if not sheet.band_rows:
        d.warnings.append("no band table")
        return d
    rows = name_rows(sheet, extra_keywords)
    d.rows = rows
    if "chainage" not in rows:
        d.warnings.append("CHAINAGE row not recognised")
        return d
    by_row: dict[str, list[Item]] = {k: [] for k in rows}
    for it in sheet.band_items:
        r = _row_of(it.y, rows)
        if r:
            by_row[r].append(it)

    # move border items that obviously belong to the neighbour row
    if "vertical" in by_row and "horizontal" in by_row:
        cls = [it for it in by_row["vertical"] if "CL" in it.text.upper().replace(" ", "")]
        for it in list(by_row["vertical"]):
            tg = parse_tagged(it.text)
            if tg and tg[0] in ("R", "TL", "LS", "LT") and \
                    not any(abs(c.x - it.x) < 4 * max(it.w, 10) for c in cls):
                by_row["vertical"].remove(it)
                by_row["horizontal"].append(it)

    marks = _marks_in(by_row["chainage"])
    cal = Calib(marks)
    if not cal.ok:
        d.warnings.append("chainage row unreadable")
        return d
    for b in cal.bad:
        d.warnings.append(f"chainage mark '{b.text}' read as {b.ch:.3f} ignored (inconsistent)")
    xs_band = [it.x for it in sheet.band_items]
    d.ch_min, d.ch_max = cal(min(cal.xs)), cal(max(cal.xs))
    lo, hi = sorted((d.ch_min, d.ch_max))
    d.ch_min, d.ch_max = lo, hi

    def mark_ok(m: Mark):
        c = cal(m.x)
        if abs(c - m.ch) < 25:
            return True
        # OCR added/dropped a km digit or the minus sign: repair when that fits the position
        for cand in (abs(m.ch), m.ch - round((m.ch - c) / 1000) * 1000,
                     abs(m.ch) - round((abs(m.ch) - c) / 1000) * 1000):
            if abs(c - cand) < 3:
                d.warnings.append(f"chainage '{m.text.strip()}' read as {m.ch:.3f}, corrected to {cand:.3f} "
                                  f"from its position")
                m.ch = cand
                return True
        return False

    def good_marks(items):
        out = []
        for m in _marks_in(items):
            if mark_ok(m):
                out.append(m)
            else:
                d.warnings.append(f"chainage '{m.text.strip()}' far from its position on the band, ignored")
        out.sort(key=lambda m: m.ch)
        uniq: list[Mark] = []
        for m in out:
            if uniq and abs(m.ch - uniq[-1].ch) < 0.05:
                continue
            uniq.append(m)
        return uniq

    def locate(center, length, marks, what):
        """Find start/end of an element of known length around `center` using the
        chainage marks; returns (start, end, how)."""
        chs = [m.ch for m in marks]
        if length:
            tol = 0.06
            pairs = [(m1, m2) for i, m1 in enumerate(chs) for m2 in chs[i + 1:]
                     if abs(m2 - m1 - length) <= tol and m1 - 5 <= center <= m2 + 5]
            if pairs:
                m1, m2 = min(pairs, key=lambda p: abs((p[0] + p[1]) / 2 - center))
                return m1, m2, "marks"
            # one mark lost its decimals in OCR (7+688 for 7+688.654): trust the precise one
            isint = lambda v: abs(v - round(v)) < 1e-9
            loose = [(m1, m2) for i, m1 in enumerate(chs) for m2 in chs[i + 1:]
                     if abs(m2 - m1 - length) <= 1.0 and (isint(m1) != isint(m2))
                     and m1 - 5 <= center <= m2 + 5]
            if loose:
                m1, m2 = min(loose, key=lambda p: abs((p[0] + p[1]) / 2 - center))
                if isint(m1):
                    d.warnings.append(f"{what}: start mark read as {m1:g}, set to {m2 - length:.3f} from end mark and length")
                    return m2 - length, m2, "end mark + length"
                d.warnings.append(f"{what}: end mark read as {m2:g}, set to {m1 + length:.3f} from start mark and length")
                return m1, m1 + length, "start mark + length"
            win = max(3.0, 0.35 * length)
            s_c = [m for m in chs if abs(m - (center - length / 2)) <= win]
            e_c = [m for m in chs if abs(m - (center + length / 2)) <= win]
            if s_c:
                m = min(s_c, key=lambda m: abs(m - (center - length / 2)))
                return m, m + length, "start mark + length"
            if e_c:
                m = min(e_c, key=lambda m: abs(m - (center + length / 2)))
                return m - length, m, "end mark + length"
            d.warnings.append(f"{what}: no chainage mark found, placed by position (+/- a few m)")
            return center - length / 2, center + length / 2, "estimated"
        left = [m for m in chs if m <= center]
        right = [m for m in chs if m > center]
        if left and right:
            return left[-1], right[0], "adjacent marks"
        d.warnings.append(f"{what}: length not written and one end off this sheet")
        return (left[-1] if left else center - 10), (right[0] if right else center + 10), "estimated"

    def tagged_items(items):
        out = []
        for it in items:
            if find_chainages(it.text):
                continue
            tg = parse_tagged(it.text)
            if tg:
                out.append((it, tg))
        return out

    def nearest(tagged, it, tags, max_dx=70, exclude_near=None):
        c = [(abs(i2.x - it.x) + abs(i2.y - it.y) * 0.5, v, i2) for i2, (t, v) in tagged
             if t in tags and abs(i2.x - it.x) <= max_dx]
        if exclude_near:   # value must be closer to this item than to another R
            c = [x for x in c if all(abs(x[2].x - it.x) <= abs(x[2].x - o.x) for o in exclude_near if o is not it)]
        return min(c, key=lambda z: z[0])[1] if c else None

    # ------------------------------------------------------------- vertical
    if "vertical" in by_row:
        its = by_row["vertical"]
        vm = good_marks(its)
        d.v_marks = [m.ch for m in vm]
        tagged = tagged_items(its)
        grades = []
        for it in its:
            if find_chainages(it.text) or parse_tagged(it.text):
                continue
            g = parse_gradient(it.text.replace(" ", ""))
            if g is not None and re.search(r"%|‰|IN", it.text.upper()):
                grades.append((it, g))
        r_items = [it for it, (t, _) in tagged if t == "R"]
        for it, (tag, val) in tagged:
            if tag != "R":
                continue
            CL = nearest(tagged, it, ("CL", "VCL", "LVC", "LV"), exclude_near=r_items)
            center = cal(it.x)
            s_, e_, how = locate(center, CL, vm, f"vertical curve R={val:g} near {center:.0f} m")
            d.vcurves.append({"R": val, "CL": CL, "start": round(s_, 3), "end": round(e_, 3),
                              "how": how, "partial": how == "estimated", "src": d.sheet.label})
            d.items_used.append(("vertical", it, f"VC R={val:g} CL={CL}", s_))
        vc_ext = [(v["start"], v["end"]) for v in d.vcurves]
        for it, g in grades:
            c = cal(it.x)
            L = nearest(tagged, it, ("L",), max_dx=120)
            left = max([e for _, e in vc_ext if e <= c] + [m.ch for m in vm if m.ch <= c], default=None)
            right = min([s_ for s_, _ in vc_ext if s_ >= c] + [m.ch for m in vm if m.ch > c], default=None)
            d.grades.append({"ch": c, "permil": g, "conf": it.conf, "raw": it.text.strip(),
                             "from": left, "to": right, "L": L, "src": d.sheet.label})
            d.items_used.append(("vertical", it, f"{g:+.3f} permil", c))

    # ----------------------------------------------------------- horizontal
    if "horizontal" in by_row:
        its = by_row["horizontal"]
        hm = good_marks(its)
        tagged = tagged_items(its)
        r_items = [it for it, (t, _) in tagged if t == "R"]
        TLTAGS = ("TL", "LS", "LT", "TRL", "SL")
        for it, (t, v) in tagged:
            d.items_used.append(("horizontal", it, f"{t}={v:g}", cal(it.x)))
        for it in r_items:
            R = parse_tagged(it.text)[1]
            L = nearest(tagged, it, ("L",), exclude_near=r_items)
            center = cal(it.x)
            s_, e_, how = locate(center, L, hm, f"curve R={R:g} near {center:.0f} m")
            d.arcs.append({"R": R, "L": L, "sc": round(s_, 3), "cs": round(e_, 3), "how": how,
                           "partial": how == "estimated", "src": d.sheet.label})
        for it, (t, v) in tagged:
            if t not in TLTAGS:
                continue
            c = cal(it.x)
            # transition belongs to the nearest arc end on its side
            best = None
            for a in d.arcs:
                if c <= a["sc"] and a["sc"] - c <= v + 25:
                    cand = (a["sc"] - c, a["sc"] - v, a["sc"])
                elif c >= a["cs"] and c - a["cs"] <= v + 25:
                    cand = (c - a["cs"], a["cs"], a["cs"] + v)
                else:
                    continue
                if best is None or cand[0] < best[0]:
                    best = cand
            if best:
                d.transitions.append({"TL": v, "start": round(best[1], 3), "end": round(best[2], 3),
                                      "src": d.sheet.label})
            else:   # arc on the next sheet: use the marks around the text
                left = [m.ch for m in hm if m.ch <= c]
                right = [m.ch for m in hm if m.ch > c]
                st_ = left[-1] if left and c - left[-1] <= v + 5 else (right[0] - v if right else c - v / 2)
                d.transitions.append({"TL": v, "start": round(st_, 3), "end": round(st_ + v, 3),
                                      "src": d.sheet.label})

    # ------------------------------------------------------- levels, cant, speed
    grid = np.array(sorted(cal.cs))

    def snap(ch):
        if len(grid):
            j = int(np.argmin(np.abs(grid - ch)))
            if abs(grid[j] - ch) < 2.5:
                return float(grid[j])
        return round(ch, 1)

    if "rail" in by_row:
        for ch, v, it in _levels_in(by_row["rail"], cal):
            d.rail.append((snap(ch), v))
    if "ground" in by_row:
        for ch, v, it in _levels_in(by_row["ground"], cal):
            d.ground.append((snap(ch), v))
    if "cant" in by_row:
        for it in by_row["cant"]:
            c = parse_cant(it.text)
            if c:
                c["ch"] = cal(it.x)
                c["src"] = d.sheet.label
                d.cant.append(c)
                d.items_used.append(("cant", it, str(c), c["ch"]))
    if "speed" in by_row:
        for it in by_row["speed"]:
            m = re.search(r"(\d{2,3})", it.text)
            if m:
                d.speed.append({"ch": cal(it.x), "kmh": float(m.group(1)), "src": d.sheet.label})
    d.ok = True
    return d
