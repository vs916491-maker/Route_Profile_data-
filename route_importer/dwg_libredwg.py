"""Read .dwg without installing anything: LibreDWG's portable dwgread.exe.

Put the unzipped LibreDWG Windows release (libredwg-<ver>-win64.zip, from
github.com/LibreDWG/libredwg/releases) in   tools/libredwg/   next to
Route_Importer.pyw  (or run get_dwg_reader.py once).  dwgread writes the
drawing as JSON; this module turns the text / line / polyline entities into an
ezdxf document, which the normal DXF reader then uses.

LibreDWG leaves entity names blank for some DWG versions (e.g. AutoCAD 2007),
so entities are recognised by their DWG type number as well.
"""
from __future__ import annotations

import glob
import json
import math
import os
import re
import shutil
import subprocess
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# DWG object type numbers (fixed by the DWG format)
T_TEXT, T_ARC, T_LINE, T_MTEXT, T_LWPOLY, T_LAYER = 1, 17, 19, 44, 77, 51
NAMES = {"TEXT": T_TEXT, "ARC": T_ARC, "LINE": T_LINE, "MTEXT": T_MTEXT, "LWPOLYLINE": T_LWPOLY}


def find_dwgread() -> str | None:
    env = os.environ.get("ROUTE_IMPORTER_DWGREAD")
    if env and os.path.isfile(env):
        return env
    for pat in ("tools/**/dwgread.exe", "tools/**/dwgread", "libredwg*/**/dwgread.exe"):
        hits = glob.glob(os.path.join(HERE, pat), recursive=True)
        if hits:
            return sorted(hits)[0]
    return shutil.which("dwgread")


def _load_json(path: str) -> dict:
    with open(path, encoding="utf-8", errors="replace") as f:
        raw = f.read()
    raw = re.sub(r"(\d)\.(\s*[\],}])", r"\1.0\2", raw)        # '4032.' -> '4032.0'
    raw = re.sub(r"(?<![\w\"])(-?nan|-?inf)(?![\w\"])", "0", raw, flags=re.I)
    return json.loads(raw, strict=False)


def _etype(o) -> int | None:
    name = (o.get("entity") or "").upper()
    if name in NAMES:
        return NAMES[name]
    t = o.get("type")
    return t if t in (T_TEXT, T_ARC, T_LINE, T_MTEXT, T_LWPOLY) else None


def read_doc(dwg_path: str, exe: str, log=print, timeout: int = 1200):
    import ezdxf
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "drawing.json")
        log("  reading DWG with LibreDWG (can take a minute for big drawings) ...")
        env = dict(os.environ)
        env["PATH"] = os.path.dirname(exe) + os.pathsep + env.get("PATH", "")   # its DLLs
        try:
            r = subprocess.run([exe, "-O", "JSON", "-o", out, dwg_path], capture_output=True, text=True,
                               timeout=timeout, env=env,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            if getattr(e, "winerror", None) in (4551, 1260, 5) or isinstance(e, PermissionError):
                raise RuntimeError(
                    "Windows Application Control blocked dwgread.exe (company security policy), "
                    "so this laptop cannot open .dwg files directly. Options: "
                    "(1) open the DWG in AutoCAD / AutoCAD LT / BricsCAD / DraftSight / LibreCAD and "
                    "'Save As' DXF, then load the .dxf here - DXF needs no converter; "
                    "(2) ask IT to allow " + exe + " (LibreDWG 0.13.3, open-source DWG reader, "
                    "no network access); (3) convert on another PC and copy the .dxf over.") from None
            raise
        if not os.path.isfile(out) or os.path.getsize(out) < 1000:
            raise RuntimeError(f"LibreDWG could not read the DWG: {(r.stderr or r.stdout)[-300:]}")
        data = _load_json(out)
    objs = data.get("OBJECTS", [])
    layers = {}
    for o in objs:
        if o.get("type") == T_LAYER and o.get("name") and isinstance(o.get("handle"), list):
            layers[o["handle"][-1]] = o["name"]
    doc = ezdxf.new("R2010")
    for n in set(layers.values()):
        try:
            if n not in doc.layers:
                doc.layers.add(n)
        except Exception:
            pass
    msp, psp = doc.modelspace(), doc.paperspace()
    n_ok = n_bad = 0
    for o in objs:
        if "entity" not in o:
            continue
        t = _etype(o)
        if t is None:
            continue
        space = psp if o.get("entmode") == 1 else msp
        lay = o.get("layer") or [0]
        att = {"layer": layers.get(lay[-1], "0") if isinstance(lay, list) else "0"}
        try:
            if t == T_TEXT:
                txt = o.get("text_value") or ""
                if not txt:
                    continue
                e = space.add_text(txt, dxfattribs=dict(att, height=o.get("height", 1.0) or 1.0,
                                                         rotation=math.degrees(o.get("rotation", 0.0) or 0.0),
                                                         insert=tuple(o["ins_pt"][:2])))
                ha, va = o.get("horiz_alignment", 0) or 0, o.get("vert_alignment", 0) or 0
                if (ha or va) and o.get("alignment_pt"):
                    e.dxf.halign, e.dxf.valign = ha, va
                    e.dxf.align_point = tuple(o["alignment_pt"][:2])
            elif t == T_MTEXT:
                txt = o.get("text") or ""
                if not txt:
                    continue
                xd = o.get("x_axis_dir") or [1, 0, 0]
                space.add_mtext(txt, dxfattribs=dict(att, insert=tuple(o["ins_pt"][:2]),
                                                     char_height=o.get("text_height", 1.0) or 1.0,
                                                     attachment_point=o.get("attachment", 1) or 1,
                                                     rotation=math.degrees(math.atan2(xd[1], xd[0]))))
            elif t == T_LINE:
                space.add_line(tuple(o["start"][:2]), tuple(o["end"][:2]), dxfattribs=att)
            elif t == T_LWPOLY:
                pts = o.get("points")
                if isinstance(pts, str):
                    pts = json.loads(pts)
                if pts and len(pts) >= 2:
                    space.add_lwpolyline([tuple(p[:2]) for p in pts], dxfattribs=att)
            elif t == T_ARC:
                space.add_arc(tuple(o["center"][:2]), o["radius"], math.degrees(o["start_angle"]),
                              math.degrees(o["end_angle"]), dxfattribs=att)
            n_ok += 1
        except Exception:
            n_bad += 1
    log(f"  DWG: {n_ok} entities read" + (f", {n_bad} skipped" if n_bad else ""))
    return doc
