# Changelog

## v0.4.1 - 2026-10-30
QA FAILs on the RS17 Line-7 ring (4 in DN, 2 in UP) traced to the tool, not the drawings - all fixed (0 FAIL):
- PVI level: where PVC, PVI and PVT each carry an 'Elev' label, the PVI's own label is taken (same
  side offset as the PVC/PVT labels) - it took the PVC level at 57+387 (grade -15.3 instead of -16.6 permil).
- Last / first tangent of a sheet (its other PVI is on the next sheet) now read from the rail levels or the
  written grade - before, the previous grade ran on over the level stretch (55+603, 59+396).
- Vertical curves at the first / last PVI of a sheet (with their PVC/PVT marks).
- Short stretches at drawing joints without a grade take the overlapping / incoming grade.
- Vertical curve PVC/PVT marks that belong to other curves no longer override the written length.
- Reading warnings outside the route's part of a profile are no longer listed.
- Tab 'Curves' renamed 'Horizontal Curves' (and Curve_Details -> Horizontal_Curve_Details). The SEC simulator
  reads it as its Curves table from rs-sec-simulator PR 'Horizontal Curves sheet' on.
- VerticalCurves and Cant tabs now simple like Curves (from_m, to_m, radius / cant, comment); the full
  columns moved to VC_Details and Cant_Details.
- VerticalCurves sheet: grade_in / grade_out / type (crest, sag) now from the tangents either side of the
  curve - before, both showed the incoming grade.
- Output file open in Excel: the route is saved under a new name (…_Route_Input_HHMMSS.xlsx) instead of failing.

## v0.4.0 - 2026-10-30
- **Route through several drawings**: give the stations of the line in running order (new "Route to build"
  box, Load/Save list, `route_stations` setting) and the tool builds ONE SEC file over all drawings on the
  chosen line (default DN). Validated on DMRC RS17 Line-7 ring Mukundpur - Lajpat Nagar - Maujpur -
  Mukundpur from 5 L-section DWGs against RS17_Line7_Route_Input_v1.3: 44/44 stations, 153/157 curves
  (DN), curve work 0.98, ring closes in level within 1.7 m (DN) / 0.5 m (UP).
- Stations matched by name with aliases; `NAME = chainage` for stations not in the drawings (ring closure).
- Branches avoided: a profile with stations not on the route is not used (by name AND position, so
  Shiv Vihar is not mistaken for Sonia Vihar).
- Sheet breaks: grade bridged from the last known grade to the next one through the PVI levels shown
  anywhere in the drawing ("Calculated", QA WARN).
- Junction stations carried between drawings when the same PVI levels appear in both.
- Unmarked profile in a drawing that shows only one line = that line.
- Grade for a short stretch cut by a vertical curve end / route end = the grade covering most of it
  (no more "no gradient" FAIL there).
- **Tunnels sheet filled from the structure marks** of CAD drawings (START/END OF U/G RAMP, C&C, TBM,
  ELEVATED RAMP, VIADUCT with their chainage); U/G ramps count as tunnel; a tunnel whose end the drawings
  do not show is cut at the end of that drawing with a QA WARN. Compare tool reports tunnel overlap.
- CLI: `-r route_list.txt -d DN`.
- Labelled profiles: split "R x" / "Lc y" texts, PVI written as "CHAINAGE= ... ELEVATION= ...",
  BVCS/EVCS marks, label strips found by position fit when there are no chainage tick rows.

## v0.3.0 - 2026-10-30
- One SEC input file per run for CAD drawings with several profiles (setting "CAD output" = single,
  default): the chosen direction (UP/DN) is joined in chainage order - a profile starting inside the
  previous one is treated as a branch (route follows it from its start). Sidings / untitled profiles left out.
  RS17 Line-7 Phase-3: UP LINE-1 28+300..53+000 + UP LINE-2 53+000..57+500 = Lajpat Nagar -> Shiv Vihar,
  21 stations, 0 FAIL / 0 WARN.
- "separate" keeps the previous one-file-per-profile output. GUI: CAD output and Direction selectors.
- settings.json: line speed 85 km/h (RS17 Line-7).

## v0.2.2 - 2026-10-30
- Clear message when Windows Application Control blocks dwgread.exe, with the DXF route and IT option.

## v0.2.1 - 2026-10-30
- .dwg files open without installing anything: portable LibreDWG `dwgread.exe` in `tools/libredwg`
  (`get_dwg_reader.py` downloads it once). ODA File Converter still used when installed.
- Works with AutoCAD 2007 DWGs where LibreDWG leaves entity names blank (types recognised by number).
- settings.json defaults updated (curve extent full, gradient change at PVT).

## v0.2.0 - 2026-10-30
Validated on DMRC RS17 Line-7 Phase-3 L-section DWG against RS17_Line7_Route_Input_v1.3.
- New reading mode for labelled CAD profiles (no ruled band table): PVI/RL, PVC/PVT, TS/SC/CS/ST,
  R/L/LS labels, station CH labels, strip titles; one output file per profile (UP LINE-1, DN LINE-1 ...).
- Reads the curve table (radius, transition, proposed cant, maximum speed) and uses it for cant and
  speed limits ("From drawing"), matched to curves by radius and line (ULC = UP, DLC = DN).
- Curve direction (sign of R) in Curve_Details.
- Station detection ignores crossovers, buffer stops, track terminations, sidings and curve codes.
- DXF reader: aligned TEXT anchored correctly; corrupt / far-away coordinates ignored.
- New settings: gradient_change_at (pvt default, vpi, pvc); curve extent default now full TS->ST
  (both match the RS17 Line-7 route input conventions).
- New: `python -m route_importer.compare` - compares a generated route with a reference route file.

## v0.1.1 - 2026-10-30
- Replaced .bat launchers (blocked on company laptops) with Python files:
  `Route_Importer.pyw` (double-click, no console), `install_packages.py`, `make_offline_bundle.py`.
- Launcher checks for missing packages / tkinter and shows a clear message; errors saved to a log file.
- Packages install with `--user` (no admin rights).

## v0.1.0 - 2026-10-30
First version.
- Reads route profile bands from DXF/DWG (CAD text), PDF text layer, or offline OCR (RapidOCR) for stroke text.
- Band rows found from table lines and named from labels (OCR-tolerant), content fallback.
- Vertical: grades, vertical curves (VPI change points); horizontal: arcs, transitions; rail/ground levels.
- Unit conversion to SEC units (%, permil, 1 in N, R/F, rise over length; km+m chainage).
- Many drawings per section: merged by chainage, stations ordered, depot sheets split into separate tracks/files.
- Excel output: template sheets unchanged + VerticalCurves, Curve_Details, Cant, Rail_Levels, QA, Sources.
- Cant from transition length / ramp, curve speeds calculated where missing (marked "Calculated").
- Offline tkinter window, command line, offline wheel install.
