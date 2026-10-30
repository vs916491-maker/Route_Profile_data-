# Changelog

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
