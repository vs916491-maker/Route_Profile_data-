"""Unit parsing and conversion to the units the SEC simulator expects.

SEC units:  chainage / length -> m,  gradient -> permil (‰, + = rising with
increasing chainage),  radius -> m,  cant -> mm,  speed -> km/h.

Every parser returns None when the text cannot be understood, so the caller can
try another interpretation (for example an upside-down OCR reading).
"""
from __future__ import annotations

import math
import re

# ---------------------------------------------------------------- OCR cleanup
_OCR_FIX = str.maketrans({"O": "0", "o": "0", "D": "0", "Q": "0", "l": "1", "I": "1",
                          "|": "1", "S": "5", "B": "8", ",": ".", "'": ".", "`": ".",
                          "·": ".", "−": "-", "–": "-", "—": "-", "＋": "+", "：": ":"})
# An upside-down string read by OCR: reversed order, and each glyph turned 180°
_FLIP = {"6": "9", "9": "6", "0": "0", "1": "1", "8": "8", "2": "2", "5": "5",
         "+": "+", ".": ".", "'": ".", ",": ".", "-": "-", "=": "=", "%": "%"}


def flip_text(s: str) -> str | None:
    """Read an OCR string as if it was upside-down. None if not flippable."""
    s = s.strip()
    out = []
    for ch in reversed(s):
        if ch not in _FLIP:
            return None
        out.append(_FLIP[ch])
    return "".join(out)


def clean_num_text(s: str) -> str:
    s = s.strip().replace(" ", "")
    # keep the tag letters (R=, L=, CL=, TL-15 ...) untouched; OCR may turn '=' into '-' or ':'
    m = re.match(r"^([A-Za-z.]{1,4})[=:\-–]([0-9OoDlIS.,'+\-]+[A-Za-z]{0,2})$", s)
    if m:
        return m.group(1).upper().replace(".", "") + "=" + m.group(2).translate(_OCR_FIX)
    if "=" in s:
        head, tail = s.split("=", 1)
        return head.upper() + "=" + tail.translate(_OCR_FIX)
    return s.translate(_OCR_FIX)


_NUM = r"[-+]?\d+(?:\.\d+)?"


def to_float(s: str) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ chainage
_KMM = re.compile(r"^(?:CH\.?:?|KM)?\s*(-?)(\d{1,4})\s*[+/]\s*(\d{1,3}(?:\.\d+)?)$", re.I)
_PLAIN = re.compile(r"^(?:CH\.?:?)?\s*(" + _NUM + r")\s*(KM|M)?$", re.I)


def parse_chainage(text: str, plain_unit: str = "m") -> float | None:
    """'0+413.787' -> 413.787 ; '12+345.6' -> 12345.6 ; '-0+363.8' -> -363.8 ;
    'CH:413.787' -> 413.787 ; '12.5 km' -> 12500 ; plain numbers use plain_unit.
    km+m form requires the metre part to have 3 integer digits (0+020, 2+816.250)."""
    if text is None:
        return None
    s = str(text).strip().upper().replace(" ", "")
    s = s.replace("CH:", "").replace("CH.", "").replace("CH", "")
    s = clean_num_text(s)
    m = _KMM.match(s)
    if m:
        sign, km, mm = m.groups()
        if len(mm.split(".")[0]) != 3:
            return None
        v = int(km) * 1000 + float(mm)
        return -v if sign else v
    m = _PLAIN.match(s)
    if m:
        v = float(m.group(1))
        unit = (m.group(2) or plain_unit).lower()
        return v * 1000 if unit == "km" else v
    return None


# ------------------------------------------------------------------ gradient
_PCT = re.compile(r"^(" + _NUM + r")%$")
_PERMIL = re.compile(r"^(" + _NUM + r")(?:‰|PERMIL|PER\s*MIL|O/OO|MM/M)$", re.I)
_ONE_IN = re.compile(r"^([+-]?)(?:([RF])\s*)?1\s*IN\s*(\d+(?:\.\d+)?)\s*([RF]|RISE|RISING|FALL|FALLING)?$", re.I)
_RF_N = re.compile(r"^([RF])\s*(\d+(?:\.\d+)?)$", re.I)              # 'R 60' = rising 1 in 60
_RISE_OVER = re.compile(r"^(" + _NUM + r")\s*M?\s*(?:IN|/|OVER|PER)\s*(" + _NUM + r")\s*M?$", re.I)


def parse_gradient(text: str, plain_unit: str = "%") -> float | None:
    """Return gradient in permil (‰). Accepts
    '-1.001%', '10‰', '+10 permil', '1 in 200', 'R 1 in 200', '1 in 200 F',
    'R 60' (Indian Railway notation), '0.5 in 100' / '0.5/100' (rise over length),
    or a bare number in plain_unit ('%', 'permil', 'ratio' = 1-in-N)."""
    if text is None:
        return None
    s = str(text).strip().replace("−", "-").replace(",", ".")
    s1 = re.sub(r"\s+", " ", s).upper()
    s1 = s1.replace("GRADIENT", "").replace("GRAD", "").strip(" :=")
    m = _PCT.match(s1.replace(" ", ""))
    if m:
        return float(m.group(1)) * 10.0
    m = _PERMIL.match(s1.replace(" ", ""))
    if m:
        return float(m.group(1))
    m = _ONE_IN.match(s1)
    if m:
        sign, rf1, n, rf2 = m.groups()
        n = float(n)
        if n == 0:
            return None
        v = 1000.0 / n
        rf = (rf1 or rf2 or "").upper()
        if sign == "-" or rf.startswith("F"):
            v = -v
        return v
    m = _RF_N.match(s1.replace(" ", ""))
    if m:
        v = 1000.0 / float(m.group(2))
        return -v if m.group(1).upper() == "F" else v
    m = _RISE_OVER.match(s1)
    if m:
        rise, length = float(m.group(1)), float(m.group(2))
        if length:
            return rise / length * 1000.0
    v = to_float(s1.replace(" ", ""))
    if v is not None:
        u = plain_unit.lower()
        if u in ("%", "pct", "percent"):
            return v * 10.0
        if u in ("permil", "‰"):
            return v
        if u in ("ratio", "1in", "1 in n") and v:
            return 1000.0 / v
    return None


def gradient_from_levels(ch1: float, z1: float, ch2: float, z2: float) -> float | None:
    """Elevation-over-length to permil."""
    if ch2 == ch1:
        return None
    return (z2 - z1) / (ch2 - ch1) * 1000.0


# --------------------------------------------------------- tagged values R= L=
_TAG = re.compile(r"^([A-Z]{1,3})\s*[=:]\s*(" + _NUM + r")\s*(MM|KM|M)?$")


def parse_tagged(text: str) -> tuple[str, float] | None:
    """'R=2200' -> ('R', 2200) ; 'CL=22.023' -> ('CL', 22.023) ; 'L=36.628'.
    Lengths given in km/mm are converted to m (cant tags handled by caller)."""
    s = clean_num_text(str(text)).upper()
    m = _TAG.match(s)
    if not m:
        return None
    tag, val, unit = m.group(1), float(m.group(2)), (m.group(3) or "")
    if unit == "KM":
        val *= 1000
    elif unit == "MM" and tag not in ("CA", "SE", "C", "E", "CD"):
        val /= 1000
    return tag, val


# --------------------------------------------------------------------- cant
def parse_cant(text: str) -> dict | None:
    """Cant / superelevation text -> {'cant_mm': .., 'ramp_n': ..}
    'Ca=110', 'SE=110mm', '110 mm', 'CANT 0.11 m', '1 in 720' (run-out ramp)."""
    s = str(text).strip().upper().replace(" ", "").replace(",", ".")
    s = s.replace("CANT", "CA").replace("SUPERELEVATION", "SE").strip("()")
    m = re.match(r"^1IN(\d+(?:\.\d+)?)$", s)
    if m:
        return {"ramp_n": float(m.group(1))}
    m = re.match(r"^(?:CA|SE|C|E)?[=:]?(" + _NUM + r")(MM|M)?$", s)
    if m and (s[0].isalpha() or m.group(2)):
        v = float(m.group(1))
        if m.group(2) == "M" or (m.group(2) is None and v < 1):
            v *= 1000
        return {"cant_mm": v}
    return None


def cant_from_ramp(runout_length_m: float, ramp_n: float) -> float:
    """Cant reached over a run-out of length L at ramp 1 in N: L/N (m) -> mm."""
    return runout_length_m / ramp_n * 1000.0


def runout_from_cant(cant_mm: float, ramp_n: float) -> float:
    return cant_mm / 1000.0 * ramp_n


def equilibrium_cant(v_kmh: float, radius_m: float, gauge_mm: float) -> float:
    """C = G V^2 / (127 R)   (G in mm -> C in mm)."""
    return gauge_mm * v_kmh ** 2 / (127.0 * radius_m)


def curve_speed_limit(radius_m: float, cant_mm: float, deficiency_mm: float,
                      gauge_mm: float) -> float:
    """V = sqrt(127 R (Ca + Cd) / G)  in km/h."""
    return math.sqrt(127.0 * radius_m * (cant_mm + deficiency_mm) / gauge_mm)


def fmt_chainage(m: float) -> str:
    sign = "-" if m < 0 else ""
    m = abs(m)
    return f"{sign}{int(m // 1000)}+{m % 1000:07.3f}"
