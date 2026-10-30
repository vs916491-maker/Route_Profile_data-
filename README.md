# Route Importer  (v0.3.0)

Offline tool: customer GAD / L-section drawings (DWG, DXF, PDF) → SEC route-input Excel
(same layout as `RS17_Line7_Route_Input_v1.3.xlsx`, plus extra tabs).

## Install (once)
1. Python 3.9+ on the laptop (python.org installer, with "tcl/tk and IDLE" ticked).
2. Double-click `install_packages.py` (installs for your user only, no admin rights).
   - No internet / pip blocked? On any PC with internet run `make_offline_bundle.py`,
     copy the whole folder (with `wheels`) to the laptop, then run `install_packages.py`.
3. For `.dwg` files (one time): double-click `get_dwg_reader.py`. It puts the portable LibreDWG
   reader (`dwgread.exe`, free, nothing to install) into `tools/libredwg`.
   If the download is blocked, get `libredwg-0.13.3-win64.zip` in your browser from
   github.com/LibreDWG/libredwg/releases and unzip it into `tools/libredwg` yourself.
   (If the ODA File Converter is installed, the tool uses that instead. A DXF never needs either.)
   If Windows Application Control blocks `dwgread.exe` (company laptops), save the DWG as DXF in any CAD
   program (AutoCAD / LT, BricsCAD, DraftSight, LibreCAD) and load the DXF, or ask IT to allow the file.

Everything then runs offline — no internet, no cloud. No `.bat` files are used
(they are often blocked on company laptops).

## Use
Double-click `Route_Importer.pyw` → add all drawings of one section (many files and pages are fine) →
check settings → **Create route Excel**.
If double-click does not start Python: right-click → *Open with* → Python, or open the file in
IDLE / VS Code and press F5.
Command line: `python -m route_importer.cli a.pdf b.dxf -o out -n BMRCL_ORR`

Output:
- `<name>_Route_Input.xlsx` – main line (banded drawings)
- labelled CAD drawing with several profiles (UP/DN, branches, sidings):
  - **CAD output = single** (default): one SEC file `<name>_UP_Route_Input.xlsx` – the chosen direction,
    branches joined in chainage order (e.g. UP LINE-1 to 53+000, then UP LINE-2 to Shiv Vihar); sidings left out
  - **CAD output = separate**: one file per profile (`<name>_UP_LINE_1_…`, `<name>_DN_LINE_1_…`, sidings …)
- `<name>_Depot_Route_Input.xlsx` – sheets whose title says DEPOT

## How it reads drawings
| Input | How |
|---|---|
| DXF / DWG (main route) | TEXT/MTEXT/block attributes read directly – exact values |
| PDF with real text | PDF text layer (pypdfium2) |
| PDF where band text is CAD strokes (like the BMRCL GAD) | offline OCR (RapidOCR) on the band rows, vertical labels read rotated |
| DXF / DWG *labelled* profiles (no ruled band table, e.g. DMRC Line-7 L-section) | reads the labels: `PVI … RL …`, `PVC/PVT`, `TS/SC/CS/ST`, `R= L=`, `LS=`, station `CH:`, strip titles (UP LINE-1, DN LINE-1 …) and the curve table (cant, maximum speed) |

Band rows are found from the long horizontal lines and named from their labels
(VERTICAL, HORIZONTAL, RAIL LEVEL, GROUND LEVEL, CHAINAGE, CANT, SPEED; OCR typos tolerated).
Extra label words can be added in `settings.json` → `row_keywords`.

## Output sheets
| Sheet | Content |
|---|---|
| Stations, Gradients, Curves, Tunnels, SpeedLimits | SEC simulator input – same columns as the template (+ optional `comment`) |
| VerticalCurves | VPI, start, end, radius, length, grade in/out, crest/sag |
| Curve_Details | TS, SC, CS, ST, radius, transitions, cant, calculated speed |
| Cant | run-out start/end, full-cant zone, cant mm, ramp 1 in N and ‰ |
| Rail_Levels | rail and ground level per chainage |
| QA | every check with OK / INFO / WARN / FAIL |
| Sources | every value read: sheet, text, confidence, method |

Yellow rows = calculated / assumed (not written in the drawing).

## Unit handling
Gradient: `%`, `‰`, `1 in N` (R/F or +/-), `R 60`/`F 34`, rise over length, or from rail levels → ‰.
Chainage: `km+m` (`2+816.250`), `CH:`, km or m → m. Cant: mm / m / `Ca=` / `SE=`, ramp `1 in N`.

## Rules for missing data (settings)
- Cant: transition length ÷ ramp (default 1 in 720), max 125 mm; no transition → equilibrium cant at line speed.
- Curve speed: V = √(127·R·(Ca+Cd)/G), Cd 100 mm, G 1500 mm, rounded down to 5 km/h, TS→ST.
- Curves sheet extent: full TS→ST (default, as in the RS17 Line-7 route input), or arc + half transitions / arc only.
- Gradient change point at each vertical curve: PVT (default, as in the RS17 Line-7 route input), VPI or PVC.
- Speed and cant: from the drawing's curve table when there is one (marked "From drawing"), otherwise calculated.
- Tunnels are not on GAD drawings – fill manually.

## Versions
See `CHANGELOG.md`. Releases are tagged (`v0.1.0`, ...). Download a tagged version from GitHub → Releases/Tags → *Download ZIP*.

**Do not commit customer drawings or generated Excel files** — this repository is public; `.gitignore` blocks them.

## Check against an existing route file
`python -m route_importer.compare generated.xlsx reference.xlsx --from_m 28400 --to_m 53500 -o comparison.xlsx`
Compares stations, gradient along the route, curves (radius, extent, curve-resistance work) and speed limits,
and writes a report workbook.
