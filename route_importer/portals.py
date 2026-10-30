"""Tunnels (underground stretches) from the structure marks of CAD drawings:

    'START OF UG RAMP', 'END OF ELEVATED RAMP & START OF U/G RAMP  UP LINE CH:13533.641',
    'END OF C&C/START OF TBM  CH:22299.408', 'START OF VIADUCT', 'END OF TBM & START OF C&C' ...

Each mark says what the track is before it (END OF ...) and/or after it (START OF ...):
T = tunnel (TBM, cut & cover, U/G ramp, covered ramp, NATM, underground), E = elevated / at grade
(viaduct, elevated ramp, at grade). Between two marks the track type follows from them; the SEC
Tunnels sheet gets every stretch of type T.  The U/G ramp (open trough) counts as tunnel, like the
RS17 Line-7 route input.
"""
from __future__ import annotations

import re

from .sources import Sheet

_MARK = re.compile(r"\b(START|END)\s+OF\b", re.I)
_T = re.compile(r"U\s*/\s*G|\bUG\b|UNDERGROUND|\bTBM\b|C\s*&\s*C|CUT\s*&\s*COVER|COVERED\s+RAMP|TUNNEL|NATM", re.I)
_E = re.compile(r"ELEVATED|VIADUCT|AT[\s-]*GRADE|EMBANKMENT", re.I)
_NOT = re.compile(r"STATION|PLATFORM|TRACK|PROFILE|ALIGNMENT|CURVE|TANK|NALA|SHAFT|ROAD|FLYOVER|BUFFER|"
                  r"BOUNDARY|SIDING|EXISTING", re.I)
_CHV = re.compile(r"(?:CH(?:AINAGE)?\s*(?:\((?:UP|DN|DOWN)\s*LINE\))?\s*[:=.]*\s*=?\s*)\(?(-)?\)?\s*(\d{1,3}\s*\+\s*\d{3}(?:\.\d+)?|\d{1,6}\.\d+)", re.I)
_LINE = re.compile(r"\b(UP|DN|DOWN)\s*LINE\b", re.I)


def _kind(phrase: str):
    if _T.search(phrase):
        return "T"
    if _E.search(phrase):
        return "E"
    return None


def _parse(text: str):
    """(before, after) track type from 'END OF X & START OF Y'."""
    before = after = None
    parts = _MARK.split(text)
    # parts: [pre, 'END', ' X & ', 'START', ' Y ...']
    for i in range(1, len(parts) - 1, 2):
        what = re.split(r"\s&\s|/|\bAND\b", parts[i + 1], maxsplit=1)[0]     # not inside 'C&C'
        k = _kind(what)
        if parts[i].upper() == "END":
            before = before or k
        else:
            after = after or k
    return before, after


def _ch(text: str):
    m = _CHV.search(text)
    if not m:
        return None
    v = m.group(2).replace(" ", "")
    if "+" in v:
        a, b = v.split("+")
        val = float(a) * 1000 + float(b)
    else:
        val = float(v)
    return -val if m.group(1) else val


def sheet_portals(sheet: Sheet) -> list[dict]:
    """Structure marks with a written chainage, in this drawing."""
    items = sheet.items
    out = []
    for it in items:
        if not _MARK.search(it.text) or len(it.text) > 90:
            continue
        h = max(min(it.w, it.h) if it.w else it.h, 0.1)
        # the mark can run on over a few stacked lines: 'END OF UG RAMP & START OF' / 'ELEVATED RAMP CH:=5649.601'
        block = [o for o in items if o is it or (abs(o.x - it.x) < 3 * h and 0 < it.y - o.y < 2.6 * h
                                                 and len(o.text) < 90)]
        block.sort(key=lambda o: -o.y)
        text = " ".join(o.text for o in block)
        if _NOT.search(re.sub(r"(START|END)\s+OF\s+(STATION|PLATFORM).*", "", text, flags=re.I)) and not _kind(text):
            continue
        before, after = _parse(text)
        if before is None and after is None:
            continue
        ch = _ch(text)
        src = text
        if ch is None:     # chainage written next to the mark
            near = [o for o in items if o is not it and abs(o.x - it.x) < 8 * h and abs(o.y - it.y) < 5 * h
                    and _ch(o.text) is not None and not _MARK.search(o.text)]
            if not near:
                continue
            o = min(near, key=lambda o: abs(o.x - it.x) + 2 * abs(o.y - it.y))
            ch, src = _ch(o.text), text + " | " + o.text
        lm = _LINE.search(src)
        line = None if not lm else ("UP" if lm.group(1).upper() == "UP" else "DN")
        out.append({"ch": ch, "before": before, "after": after, "line": line, "text": src[:120],
                    "file": re.split(r"[\\/]", sheet.source)[-1]})
    return out


def route_tunnels(marks: list[dict], direction: str, lo: float, hi: float, file_spans: dict):
    """Tunnel stretches between lo and hi from the marks.  file_spans: file -> (min, max) chainage
    the route takes from that drawing (a tunnel left open at the last mark of a drawing ends there).
    Returns (tunnels, notes)."""
    # a drawing's marks count only on the stretch the route takes from that drawing
    # (depot / local chainages elsewhere in the same file are ignored)
    ev = [m for m in marks if lo - 50 <= m["ch"] <= hi + 50 and m["file"] in file_spans
          and file_spans[m["file"]][0] - 1000 <= m["ch"] <= file_spans[m["file"]][1] + 1000]
    # the other line's mark only where this line has none close by
    mine = [m for m in ev if m["line"] in (direction, None)]
    ev = mine + [m for m in ev if m["line"] not in (direction, None)
                 and not any(abs(m["ch"] - x["ch"]) < 60 for x in mine)]
    ev.sort(key=lambda m: m["ch"])
    merged = []
    for m in ev:                       # the same mark written several times
        if merged and abs(m["ch"] - merged[-1]["ch"]) < 20:
            p = merged[-1]
            p["before"] = p["before"] or m["before"]
            p["after"] = p["after"] or m["after"]
            continue
        merged.append(dict(m))
    notes = []
    if not merged:
        return [], ["no tunnel / viaduct marks with chainage in the drawings - Tunnels sheet left empty"]
    pts = [lo] + [min(max(m["ch"], lo), hi) for m in merged] + [hi]
    tun = []
    for i, (a, b) in enumerate(zip(pts, pts[1:])):
        if b - a < 1:
            continue
        left = merged[i - 1] if i > 0 else None
        right = merged[i] if i < len(merged) else None
        k_l = left["after"] if left else None
        k_r = right["before"] if right else None
        if k_l and k_r and k_l != k_r:
            notes.append(f"track type between {a:.0f} and {b:.0f} m unclear ('{left['text'][:40]}' / "
                         f"'{right['text'][:40]}') - taken as {k_l}")
        k = k_l or k_r
        if k is None and left and right and left["before"] and left["before"] == right["after"]:
            k = left["before"]          # 'END OF UG RAMP' ... 'START OF UG RAMP': tunnel on both sides
        if k != "T":
            continue
        # an open end: the marks of one drawing do not say where the tunnel ends
        if left and not k_r and (right is None or right["file"] != left["file"]):
            end = min(b, file_spans.get(left["file"], (a, b))[1])
            if end < b - 1:
                notes.append(f"tunnel from {a:.0f} m: the drawings do not show where it ends - taken to the end "
                             f"of {left['file']} ({end:.0f} m); check")
            b = end
        if right and not k_l and (left is None or left["file"] != right["file"]):
            start = max(a, file_spans.get(right["file"], (a, b))[0])
            if start > a + 1:
                notes.append(f"tunnel to {b:.0f} m: the drawings do not show where it starts - taken from the start "
                             f"of {right['file']} ({start:.0f} m); check")
            a = start
        if b - a < 1:
            continue
        if tun and a - tun[-1][1] < 1:
            tun[-1][1] = b
        else:
            tun.append([a, b])
    return [tuple(t) for t in tun], notes
