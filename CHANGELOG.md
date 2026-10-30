# Changelog

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
