"""Run the whole import: drawings -> route workbook(s)."""
from __future__ import annotations

import os
import re

from . import excel_out
from .bands import interpret
from .cadlabels import assemble, interpret_sheet as interpret_labels, parse_route, transfer_stations
from .cadprofile import chain_profiles
from .merge import build_route
from .sources import read_any


def run(files: list[str], out_dir: str, project: str, cfg: dict, log=print, sheets=None) -> list[str]:
    if sheets is None:
        sheets = []
        for f in files:
            try:
                sheets += read_any(f, cfg["ocr"], int(cfg["ocr_dpi"]), log)
            except Exception as e:           # one bad file must not stop the others
                log(f"  ERROR reading {os.path.basename(f)}: {e}")
    outs = []
    jobs = []
    # CAD profiles without a ruled band table (labelled PVI / TS / SC ... texts)
    banded, profs_all = [], []
    for s in sheets:
        if s.band_rows or not s.source.lower().endswith((".dxf", ".dwg")):
            banded.append(s)
            continue
        if len(s.items) < 200:          # title-block layouts etc.
            continue
        log(f"{s.label}: no band table - reading as labelled CAD profile")
        profs_all += interpret_labels(s, log)
    if profs_all:
        transfer_stations(profs_all, log)
        route = parse_route(cfg.get("route_stations", ""))
        dirn = cfg.get("cad_direction", "DN")
        aliases = {k.upper(): [x.upper() for x in v] for k, v in (cfg.get("station_aliases") or {}).items()}
        if route:
            joined, notes = assemble(profs_all, dirn, route, aliases, log)
            if joined is None:
                log("  route not built: " + "; ".join(notes))
            else:
                log(f"  single SEC route ({dirn} line): {joined.info[0]}")
                for n in notes:
                    log("  note: " + n)
                jobs.append((f"_{dirn}", [joined], True))
        elif cfg.get("cad_output", "single") == "single":
            joined = chain_profiles(profs_all, dirn)
            if joined is None:
                log(f"  no {dirn} profile found - writing every profile separately")
                for d in profs_all:
                    jobs.append(("_" + re.sub(r"[^A-Za-z0-9]+", "_", d.sheet.name).strip("_"), [d], not d.sheet.is_depot))
            else:
                log(f"  single SEC route ({dirn}): {joined.info[0]}")
                left = [d.sheet.name for d in profs_all if d.sheet.name not in joined.sheet.name]
                if left:
                    log("  left out: " + ", ".join(left) + "  (choose 'separate' output to get them)")
                jobs.append((f"_{dirn}", [joined], True))
        else:
            for d in profs_all:
                tag = "_" + re.sub(r"[^A-Za-z0-9]+", "_", d.sheet.name).strip("_")
                jobs.append((tag, [d], not d.sheet.is_depot))
    sheets = banded
    main = [s for s in sheets if not s.is_depot]
    depot = [s for s in sheets if s.is_depot]
    if main:
        log(f"Interpreting {len(main)} main-line sheet(s) ...")
        jobs.append(("", [interpret(s, cfg.get("row_keywords")) for s in main], True))
    if depot:
        log(f"Interpreting {len(depot)} depot sheet(s) ...")
        ddat = [interpret(s, cfg.get("row_keywords")) for s in depot]
        tracks = split_tracks(ddat)
        for i, tr in enumerate(tracks, 1):
            tag = "_Depot" if len(tracks) == 1 else f"_Depot{i}"
            log(f"  depot track {i}: " + ", ".join(d.sheet.name for d in tr))
            jobs.append((tag, tr, False))
    for label, datas, stations in jobs:
        group = [d.sheet for d in datas]
        route = build_route(project + label.replace("_", " "), datas, group, cfg, with_stations=stations)
        if not any(d.ok for d in datas):
            log("  nothing readable - no file written")
            continue
        path = os.path.join(out_dir, f"{project}{label}_Route_Input.xlsx")
        try:
            excel_out.write(route, path, cfg, files)
        except PermissionError:
            # the old file is open in Excel (Windows locks it): write a new file next to it
            import time
            old = os.path.basename(path)
            path = os.path.join(out_dir, f"{project}{label}_Route_Input_{time.strftime('%H%M%S')}.xlsx")
            log(f"  {old} is open in Excel (or read-only) - saved as {os.path.basename(path)} instead. "
                "Close the old file in Excel to keep the usual name next time.")
            excel_out.write(route, path, cfg, files)
        nf = sum(q["status"] == "FAIL" for q in route.qa)
        nw = sum(q["status"] == "WARN" for q in route.qa)
        log(f"  -> {os.path.basename(path)}: {len(route.stations)} stations, {len(route.gradients)} gradients, "
            f"{len(route.curves)} curves, {len(route.vcurves)} vertical curves | QA {nf} FAIL, {nw} WARN")
        outs.append(path)
    return outs


def split_tracks(datas):
    """Depot drawings often show several tracks, each with its own chainage.
    Sheets join the same track only if their chainages connect and their rail
    levels agree where they overlap."""
    import numpy as np
    good = sorted([d for d in datas if d.ok], key=lambda d: d.ch_min)
    tracks: list[list] = []
    for d in good:
        placed = False
        for tr in tracks:
            lo = min(x.ch_min for x in tr); hi = max(x.ch_max for x in tr)
            if d.ch_min > hi + 50 or d.ch_max < lo - 50:
                continue
            lv = {round(c, 1): v for x in tr for c, v in x.rail}
            common = [abs(lv[round(c, 1)] - v) for c, v in d.rail if round(c, 1) in lv]
            if len(common) >= 3 and float(np.median(common)) > 0.05:
                continue
            tr.append(d); placed = True
            break
        if not placed:
            tracks.append([d])
    return tracks or [datas]
