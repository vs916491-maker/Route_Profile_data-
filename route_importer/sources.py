"""Readers: turn a PDF page, a DXF/DWG drawing into a common `Sheet`.

A Sheet is a list of positioned text items + long horizontal lines, with
y growing downwards (like an image). Everything after this module is the same
whatever the input format was.
"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Item:
    text: str
    x: float           # centre
    y: float           # centre, y down
    w: float = 0.0
    h: float = 0.0
    conf: float = 1.0
    method: str = "text"   # text | ocr | dxf


@dataclass
class Sheet:
    source: str
    name: str                     # 'p01' or DXF layout name
    width: float
    height: float
    items: list[Item] = field(default_factory=list)       # all text
    lines: list[str] = field(default_factory=list)        # raw text lines (title, stations)
    hlines: list[tuple[float, float, float]] = field(default_factory=list)  # (y, x0, x1)
    band_items: list[Item] = field(default_factory=list)  # text inside the band area
    band_rows: list[tuple[float, float]] = field(default_factory=list)
    is_depot: bool = False
    drawing_no: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{os.path.basename(self.source)} [{self.name}]"


# ============================================================ band geometry
def band_cluster(ys: list[float], height: float, min_lines: int = 4) -> list[float]:
    """From horizontal line y positions find the profile-band table: a run of
    >= min_lines lines spaced 1 %..7 % of the sheet height apart."""
    ys = sorted(set(round(y, 1) for y in ys))
    merged: list[float] = []
    for y in ys:
        if merged and y - merged[-1] < height * 0.004:
            continue
        merged.append(y)
    best: list[float] = []
    cur: list[float] = []
    for y in merged:
        if cur and height * 0.008 <= y - cur[-1] <= height * 0.045:
            cur.append(y)
        else:
            if len(cur) > len(best):
                best = cur
            cur = [y]
    if len(cur) > len(best):
        best = cur
    return best if len(best) >= min_lines else []


def band_cluster_scalefree(hl: list[tuple[float, float, float]], min_lines: int = 4) -> list[float]:
    """Band table in CAD model space (any units / extent): a run of long parallel
    lines whose spacing is small compared with their length and roughly uniform."""
    if not hl:
        return []
    Lmax = max(x1 - x0 for _, x0, x1 in hl)
    lines = sorted((y, x0, x1) for y, x0, x1 in hl if x1 - x0 >= 0.3 * Lmax)
    merged = []
    for ln in lines:
        if merged and abs(ln[0] - merged[-1][0]) < 1e-3 * Lmax:
            continue
        merged.append(ln)
    best, cur = [], []
    for ln in merged:
        if cur:
            g = ln[0] - cur[-1][0]
            L = min(ln[2] - ln[1], cur[-1][2] - cur[-1][1])
            overlap = min(ln[2], cur[-1][2]) - max(ln[1], cur[-1][1])
            pg = cur[-1][0] - cur[-2][0] if len(cur) > 1 else g
            if g <= 0.06 * L and overlap >= 0.8 * L and 0.33 <= g / pg <= 3:
                cur.append(ln)
                continue
            if len(cur) > len(best):
                best = cur
        cur = [ln]
    if len(cur) > len(best):
        best = cur
    return [ln[0] for ln in best] if len(best) >= min_lines else []


def table_lines(rows: list[float], hl, width: float) -> list[float]:
    """Keep only real table borders: they start at the band label column (left-most x).
    Long diagram lines inside a band (curvature line, grade line) start further right."""
    if not rows:
        return rows
    x0s = {y: min((x0 for yy, x0, _ in hl if abs(yy - y) < 0.8 * max(1e-9, width / 1000) or abs(yy - y) < 0.8),
                  default=0.0) for y in rows}
    left = min(x0s.values())
    kept = [y for y in rows if x0s[y] <= left + 0.02 * width]
    return kept if len(kept) >= 3 else rows


def hlines_from_image(img: np.ndarray, scale: float, min_frac: float = 0.35):
    """Long horizontal lines in a rendered page (any colour, not white)."""
    g = img.min(axis=2) if img.ndim == 3 else img
    mask = g < 200
    H, W = mask.shape
    out = []
    for r in range(H):
        row = mask[r]
        if row.sum() < W * min_frac:
            continue
        d = np.diff(np.concatenate(([0], row.view(np.int8), [0])))
        starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
        k = int(np.argmax(ends - starts))
        # allow small gaps: merge runs separated by < 0.3 % of width
        s, e = starts[k], ends[k]
        for s2, e2 in zip(starts, ends):
            if s2 > e and s2 - e < W * 0.003:
                e = e2
        for s2, e2 in zip(starts[::-1], ends[::-1]):
            if e2 < s and s - e2 < W * 0.003:
                s = s2
        if e - s >= W * min_frac:
            out.append((r / scale, s / scale, e / scale))
    return out


# ================================================================ PDF reader
_DEPOT = re.compile(r"\bDEPOT\b", re.I)
_DWGNO = re.compile(r"[A-Z0-9]+(?:/[A-Z0-9\-]+){2,}/\d{2,4}")


def _page_transform(page):
    """Map unrotated PDF user space -> displayed page (x right, y down)."""
    l0, b0, r0, t0 = page.get_mediabox()
    Wm, Hm = r0 - l0, t0 - b0
    rot = page.get_rotation() % 360

    def f(ux, uy):
        ux, uy = ux - l0, uy - b0
        if rot == 90:
            return uy, ux
        if rot == 180:
            return Wm - ux, uy
        if rot == 270:
            return Hm - uy, Wm - ux
        return ux, Hm - uy
    return f


def _pdf_words(tp, tf):
    """Group pdfium chars into words with bounding boxes (displayed, y down)."""
    words, lines = [], []
    n = tp.count_chars()
    cur, box = [], None
    line_buf: list[str] = []

    def flush():
        nonlocal cur, box
        if cur:
            l, b, r, t = box
            xa, ya = tf(l, b)
            xb, yb = tf(r, t)
            words.append(Item("".join(cur), (xa + xb) / 2, (ya + yb) / 2,
                              abs(xb - xa), abs(yb - ya), 1.0, "text"))
        cur, box = [], None

    for i in range(n):
        ch = tp.get_text_range(i, 1)
        if ch in ("\r", "\n"):
            flush()
            if ch == "\n":
                lines.append("".join(line_buf))
                line_buf = []
            continue
        line_buf.append(ch)
        if ch.isspace():
            flush()
            continue
        try:
            l, b, r, t = tp.get_charbox(i)
        except Exception:
            continue
        if box is not None:
            pl, pb, pr, pt = box
            size = max(pt - pb, pr - pl, 1e-6)
            gap = min(abs(l - pr), abs(b - pt), abs(pb - t), abs(pl - r))
            if gap > size * 1.5:
                flush()
        cur.append(ch)
        box = (l, b, r, t) if box is None else (min(box[0], l), min(box[1], b), max(box[2], r), max(box[3], t))
    flush()
    if line_buf:
        lines.append("".join(line_buf))
    return words, lines


def read_pdf(path: str, use_ocr: str = "auto", ocr_dpi: int = 300, log=print,
             pages: list[int] | None = None) -> list[Sheet]:
    """use_ocr: 'auto' (OCR only when the band has no real text), 'always', 'never'."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(path)
    sheets = []
    ocr = None
    idx = pages if pages else range(len(pdf))
    for pi in idx:
        page = pdf[pi]
        W, H = page.get_size()
        tp = page.get_textpage()
        words, lines = _pdf_words(tp, _page_transform(page))
        full = "\n".join(lines)
        sh = Sheet(path, f"p{pi + 1:02d}", W, H, items=words, lines=lines)
        sh.is_depot = bool(_DEPOT.search(full))
        m = _DWGNO.search(full)
        sh.drawing_no = m.group(0) if m else ""

        # band lines from a light render
        low = 100 / 72
        img = np.asarray(page.render(scale=low).to_pil().convert("RGB"))
        sh.hlines = hlines_from_image(img, low)
        rows = table_lines(band_cluster([y for y, _, _ in sh.hlines], H), sh.hlines, W)
        if not rows:
            sh.notes.append("no profile band table found on this page")
            log(f"  {sh.label}: no profile band table found")
            sheets.append(sh)
            continue
        sh.band_rows = list(zip(rows[:-1], rows[1:]))
        y0, y1 = rows[0] - 2, rows[-1] + 2
        real = [w for w in words if y0 <= w.y <= y1]
        numeric = [w for w in real if re.search(r"\d", w.text)]
        need_ocr = use_ocr == "always" or (use_ocr == "auto" and len(numeric) < 10)
        if need_ocr:
            if ocr is None:
                ocr = _get_ocr()
            ocr_items = ocr_bands(page, ocr, sh, ocr_dpi)
            sh.band_items = ocr_items + [w for w in real if not re.search(r"\d", w.text)]
            log(f"  {sh.label}: {len(rows) - 1} band rows, OCR read {len(ocr_items)} texts")
        else:
            sh.band_items = real
            log(f"  {sh.label}: {len(rows) - 1} band rows, {len(real)} texts from PDF text layer")
        sheets.append(sh)
    return sheets


def _get_ocr():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


def _ocr_array(ocr, arr, chunk_px, overlap_px, axis):
    """OCR an image array in overlapping chunks along `axis` (1 = x, 0 = y).
    Returns (text, cx, cy, w, h, conf) in array pixels."""
    out = []
    n = arr.shape[axis]
    start = 0
    while start < n:
        end = min(n, start + chunk_px)
        part = arr[:, start:end] if axis == 1 else arr[start:end, :]
        ph, pw = part.shape[:2]
        if min(ph, pw) < 736:   # the detector scales the short side to 736 px: pad thin strips instead
            canvas = np.full((max(ph, 736), max(pw, 736), 3), 255, dtype=np.uint8)
            canvas[:ph, :pw] = part
            part = canvas
        res, _ = ocr(np.ascontiguousarray(part))
        for pts, txt, conf in res or []:
            pts = np.asarray(pts, dtype=float)
            cx, cy = pts[:, 0].mean(), pts[:, 1].mean()
            if axis == 1:
                cx += start
            else:
                cy += start
            out.append((str(txt).strip(), cx, cy, np.ptp(pts[:, 0]), np.ptp(pts[:, 1]), float(conf)))
        if end == n:
            break
        start = end - overlap_px
    return out


def ocr_region(page, ocr, box, dpi=300, chunk_px=1200, overlap_px=150,
               normal_rows=None, rotated_rows=None) -> list[Item]:
    """OCR band rows of a pdfium page (pt, y down).
    normal_rows : rows read as drawn (horizontal text: R=, L=, %, labels)
    rotated_rows: rows also read turned 90 deg clockwise, so labels written
                  vertically (chainages, levels) become horizontal for the OCR."""
    x0, y0, x1, y1 = box
    W, H = page.get_size()
    scale = dpi / 72
    pad = 12
    y0, y1 = max(0, y0 - pad), min(H, y1 + pad)
    img = page.render(scale=scale, crop=(x0, H - y1, W - x1, y0)).to_pil().convert("RGB")
    arr = np.asarray(img)
    items: list[Item] = []

    def rows_px(rows, margin=0.35):
        for ry0, ry1 in rows:
            m = (ry1 - ry0) * margin
            a = max(0, int((ry0 - m - y0) * scale))
            b = min(arr.shape[0], int((ry1 + m - y0) * scale))
            if b - a >= 10:
                yield a, b

    for a, b in rows_px(normal_rows if normal_rows is not None else [(y0, y1)]):
        for txt, cx, cy, w, h, conf in _ocr_array(ocr, arr[a:b], 2400, 300, 1):
            if w >= 0.8 * h:                      # horizontal text only from this pass
                items.append(Item(txt, x0 + cx / scale, y0 + (a + cy) / scale, w / scale, h / scale, conf, "ocr"))
    for a, b in rows_px(rotated_rows or [], 0.12):
        sub = np.rot90(arr[a:b], k=-1)            # clockwise: bottom-to-top text reads left-to-right
        hh = b - a
        for txt, cx, cy, w, h, conf in _ocr_array(ocr, sub, chunk_px, overlap_px, 0):
            if w < 0.8 * h:
                continue
            ox, oy = cy, a + (hh - 1 - cx)        # back to the unrotated crop
            items.append(Item(txt, x0 + ox / scale, y0 + oy / scale, h / scale, w / scale, conf, "ocr"))
    items.sort(key=lambda it: -it.conf)
    kept: list[Item] = []
    for it in items:
        if any(abs(it.x - k.x) < 2.5 and abs(it.y - k.y) < 2.5 for k in kept):
            continue
        kept.append(it)
    return kept


def ocr_bands(page, ocr, sh: "Sheet", dpi: int, skip=("ground", "other")) -> list[Item]:
    """Read the band label column first, then OCR each row the way its content is written."""
    from .bands import name_rows
    W, H = page.get_size()
    y0, y1 = sh.band_rows[0][0], sh.band_rows[-1][1]
    labels = ocr_region(page, ocr, (0, y0, W * 0.16, y1), dpi, normal_rows=sh.band_rows)
    tmp = Sheet(sh.source, sh.name, W, H, band_items=labels, band_rows=sh.band_rows)
    named = name_rows(tmp)
    kind = {v: k for k, v in named.items()}
    normal, rotated = [], []
    for r in sh.band_rows:
        k = kind.get(r, "unknown")
        if k in skip:
            continue
        if k in ("vertical", "horizontal", "cant", "speed", "unknown"):
            normal.append(r)
        if k in ("chainage", "rail", "vertical", "horizontal", "cant", "speed", "unknown"):
            rotated.append(r)
    data = ocr_region(page, ocr, (0, y0, W, y1), dpi, normal_rows=normal, rotated_rows=rotated)
    return labels + data


# ================================================================ DXF reader
def find_oda() -> str | None:
    """Locate ODA File Converter (needed only for .dwg)."""
    for name in ("ODAFileConverter", "ODAFileConverter.exe"):
        p = shutil.which(name)
        if p:
            return p
    roots = [os.environ.get("ProgramFiles", r"C:\Program Files"),
             os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
             os.path.expanduser("~")]
    for root in roots:
        oda = os.path.join(root, "ODA")
        if os.path.isdir(oda):
            for d in sorted(os.listdir(oda), reverse=True):
                exe = os.path.join(oda, d, "ODAFileConverter.exe")
                if os.path.isfile(exe):
                    return exe
    return None


def read_dwg(path: str, log=print) -> list[Sheet]:
    from ezdxf.addons import odafc
    exe = find_oda()
    if not exe:
        raise RuntimeError(
            "Reading .dwg needs the free 'ODA File Converter' (opendesign.com). "
            "Install it, or open the DWG in any CAD viewer and 'Save As' DXF, "
            "then load the .dxf here.")
    import ezdxf
    ezdxf.options.set("odafc-addon", "win_exec_path" if os.name == "nt" else "unix_exec_path", exe)
    doc = odafc.readfile(path)
    return _sheets_from_doc(doc, path, log)


def read_dxf(path: str, log=print) -> list[Sheet]:
    import ezdxf
    from ezdxf import recover
    try:
        doc = ezdxf.readfile(path)
    except Exception:
        doc, _ = recover.readfile(path)
    return _sheets_from_doc(doc, path, log)


def _plain(e) -> str:
    t = e.dxftype()
    if t == "MTEXT":
        return e.plain_text().replace("\n", " ").strip()
    if t in ("TEXT", "ATTRIB"):
        return (e.dxf.text or "").strip()
    return ""


def _collect(space):
    """Yield text/line entities, exploding block references (virtual)."""
    for e in space:
        t = e.dxftype()
        if t == "INSERT":
            try:
                for v in e.virtual_entities():
                    yield from _collect([v])
                for a in e.attribs:
                    yield a
            except Exception:
                continue
        else:
            yield e


def _sheets_from_doc(doc, path, log) -> list[Sheet]:
    sheets = []
    spaces = [("Model", doc.modelspace())] + [(l.name, l) for l in doc.layouts if l.name != "Model"]
    for name, space in spaces:
        items, segs = [], []
        for e in _collect(space):
            t = e.dxftype()
            try:
                if t in ("TEXT", "MTEXT", "ATTRIB"):
                    s = _plain(e)
                    if not s:
                        continue
                    ins = e.dxf.insert
                    h = float((e.dxf.char_height if t == "MTEXT" else e.dxf.height) or 1.0)
                    rot = float(e.dxf.get("rotation", 0) or 0)
                    # approximate centre: shift half the text length along the rotation
                    L = 0.6 * h * len(s)
                    import math
                    c, sn = math.cos(math.radians(rot)), math.sin(math.radians(rot))
                    cx, cy = ins.x + c * L / 2 - sn * h / 2, ins.y + sn * L / 2 + c * h / 2
                    if t == "MTEXT":
                        cx, cy = ins.x, ins.y
                    for part in re.split(r"\s{2,}", s):
                        items.append(Item(part.strip(), cx, cy, L if abs(c) > .5 else h,
                                          h if abs(c) > .5 else L, 1.0, "dxf"))
                elif t == "LINE":
                    a, b = e.dxf.start, e.dxf.end
                    segs.append((a.x, a.y, b.x, b.y))
                elif t == "LWPOLYLINE":
                    pts = [(p[0], p[1]) for p in e.get_points()]
                    segs += [(p[0], p[1], q[0], q[1]) for p, q in zip(pts, pts[1:])]
            except Exception:
                continue
        if not items:
            continue
        ys = [it.y for it in items] + [s[1] for s in segs]
        xs = [it.x for it in items] + [s[0] for s in segs]
        ymax, ymin = max(ys), min(ys)
        xmin, xmax = min(xs), max(xs)
        W, H = xmax - xmin, ymax - ymin
        for it in items:                       # flip to y-down, origin top-left
            it.x, it.y = it.x - xmin, ymax - it.y
        hl = []
        for x0, y0, x1, y1 in segs:
            if abs(y1 - y0) < max(abs(x1 - x0), 1e-9) * 1e-3 and abs(x1 - x0) > W * 0.05:
                hl.append((ymax - y0, min(x0, x1) - xmin, max(x0, x1) - xmin))
        sh = Sheet(path, name, W, H, items=items, lines=[it.text for it in items], hlines=hl)
        full = " ".join(sh.lines)
        sh.is_depot = bool(_DEPOT.search(full))
        m = _DWGNO.search(full)
        sh.drawing_no = m.group(0) if m else ""
        rows = table_lines(band_cluster_scalefree(hl), hl, W)
        if rows:
            sh.band_rows = list(zip(rows[:-1], rows[1:]))
            sh.band_items = [it for it in items if rows[0] - 1e-6 <= it.y <= rows[-1] + 1e-6]
            log(f"  {sh.label}: {len(rows) - 1} band rows, {len(sh.band_items)} texts")
        else:
            sh.notes.append("no profile band table (long horizontal lines) found")
            log(f"  {sh.label}: no profile band table found")
        sheets.append(sh)
    return sheets


def read_any(path: str, use_ocr="auto", ocr_dpi=300, log=print) -> list[Sheet]:
    ext = os.path.splitext(path)[1].lower()
    log(f"Reading {os.path.basename(path)}")
    if ext == ".pdf":
        return read_pdf(path, use_ocr, ocr_dpi, log)
    if ext == ".dxf":
        return read_dxf(path, log)
    if ext == ".dwg":
        return read_dwg(path, log)
    raise ValueError(f"Unsupported file type: {ext}")
