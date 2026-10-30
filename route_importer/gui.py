"""Offline desktop window (tkinter, built into Python)."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import config
from .pipeline import run

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS = os.path.join(HERE, "settings.json")

FIELDS = [  # key, label, kind
    ("line_speed_kmh", "Line speed (km/h)", float),
    ("dwell_s", "Dwell (s)", float),
    ("terminal_dwell_s", "Terminal dwell (s)", float),
    ("cant_ramp_1_in", "Cant ramp, 1 in", float),
    ("max_cant_mm", "Max cant (mm)", float),
    ("cant_deficiency_mm", "Cant deficiency (mm)", float),
    ("cant_gauge_mm", "G for cant (mm)", float),
    ("speed_round_down_kmh", "Round speed down to (km/h)", float),
    ("ocr_dpi", "OCR resolution (dpi)", int),
]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Route Importer - GAD drawings to SEC route input")
        self.geometry("980x700")
        self.cfg = config.load(SETTINGS)
        self.files: list[str] = []
        self.q: queue.Queue = queue.Queue()
        self._build()
        self.after(150, self._poll)

    # ------------------------------------------------------------------ layout
    def _build(self):
        pad = {"padx": 6, "pady": 4}
        top = ttk.Frame(self); top.pack(fill="x", **pad)
        ttk.Label(top, text="Project / line name").pack(side="left")
        self.project = tk.StringVar(value="Route")
        ttk.Entry(top, textvariable=self.project, width=30).pack(side="left", padx=6)
        ttk.Label(top, text="Output folder").pack(side="left", padx=(20, 0))
        self.out = tk.StringVar(value=os.path.expanduser("~"))
        ttk.Entry(top, textvariable=self.out, width=40).pack(side="left", padx=6)
        ttk.Button(top, text="...", width=3, command=self._pick_out).pack(side="left")

        mid = ttk.Frame(self); mid.pack(fill="both", expand=False, **pad)
        lf = ttk.LabelFrame(mid, text="Drawings (PDF, DXF, DWG) - one section can have many files")
        lf.pack(side="left", fill="both", expand=True)
        self.lb = tk.Listbox(lf, height=10, selectmode="extended")
        self.lb.pack(fill="both", expand=True, padx=4, pady=4)
        bf = ttk.Frame(lf); bf.pack(fill="x")
        ttk.Button(bf, text="Add files", command=self._add).pack(side="left", padx=4, pady=4)
        ttk.Button(bf, text="Remove", command=self._remove).pack(side="left")
        ttk.Button(bf, text="Clear", command=self._clear).pack(side="left", padx=4)

        sf = ttk.LabelFrame(mid, text="Settings (used when the drawing has no value)")
        sf.pack(side="left", fill="y", padx=(8, 0))
        self.vars = {}
        for i, (k, lab, _) in enumerate(FIELDS):
            ttk.Label(sf, text=lab).grid(row=i, column=0, sticky="w", padx=4, pady=1)
            v = tk.StringVar(value=str(self.cfg[k])); self.vars[k] = v
            ttk.Entry(sf, textvariable=v, width=9).grid(row=i, column=1, padx=4)
        i = len(FIELDS)
        ttk.Label(sf, text="Curve extent for SEC").grid(row=i, column=0, sticky="w", padx=4)
        self.extent = tk.StringVar(value=self.cfg["curve_extent_for_sec"])
        ttk.Combobox(sf, textvariable=self.extent, width=24, state="readonly",
                     values=["arc", "arc_plus_half_transitions", "full"]).grid(row=i, column=1, padx=4)
        ttk.Label(sf, text="OCR for PDF").grid(row=i + 1, column=0, sticky="w", padx=4)
        self.ocr = tk.StringVar(value=self.cfg["ocr"])
        ttk.Combobox(sf, textvariable=self.ocr, width=24, state="readonly",
                     values=["auto", "always", "never"]).grid(row=i + 1, column=1, padx=4)
        self.cmt = tk.BooleanVar(value=self.cfg["sec_comment_column"])
        ttk.Checkbutton(sf, text="Comment column in SEC sheets", variable=self.cmt).grid(
            row=i + 2, column=0, columnspan=2, sticky="w", padx=4)

        rf = ttk.Frame(self); rf.pack(fill="x", **pad)
        self.btn = ttk.Button(rf, text="Create route Excel", command=self._run)
        self.btn.pack(side="left")
        ttk.Button(rf, text="Open output folder", command=self._open_out).pack(side="left", padx=8)
        self.pb = ttk.Progressbar(rf, mode="indeterminate", length=200); self.pb.pack(side="left", padx=8)

        self.log = tk.Text(self, height=18, wrap="word", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, **pad)
        self._say("Add the customer drawings for one line section, check the settings, then Create route Excel.\n"
                  "DXF/DWG text is read directly. PDF text drawn as CAD strokes is read by offline OCR "
                  "(about 5-10 s per sheet).\n")

    # --------------------------------------------------------------- actions
    def _say(self, s):
        self.log.insert("end", s + ("\n" if not s.endswith("\n") else "")); self.log.see("end")

    def _pick_out(self):
        d = filedialog.askdirectory(initialdir=self.out.get())
        if d:
            self.out.set(d)

    def _add(self):
        fs = filedialog.askopenfilenames(filetypes=[("Drawings", "*.pdf *.dxf *.dwg"), ("All", "*.*")])
        for f in fs:
            if f not in self.files:
                self.files.append(f); self.lb.insert("end", f)
        if fs and self.project.get() == "Route":
            self.project.set(os.path.splitext(os.path.basename(fs[0]))[0][:30].replace(" ", "_"))
        if fs:
            self.out.set(os.path.dirname(fs[0]))

    def _remove(self):
        for i in reversed(self.lb.curselection()):
            self.lb.delete(i); del self.files[i]

    def _clear(self):
        self.lb.delete(0, "end"); self.files.clear()

    def _open_out(self):
        p = self.out.get()
        if sys.platform.startswith("win"):
            os.startfile(p)  # noqa
        else:
            subprocess.Popen(["xdg-open", p])

    def _collect_cfg(self):
        for k, _, kind in FIELDS:
            self.cfg[k] = kind(float(self.vars[k].get()))
        self.cfg["curve_extent_for_sec"] = self.extent.get()
        self.cfg["ocr"] = self.ocr.get()
        self.cfg["sec_comment_column"] = self.cmt.get()
        config.save(self.cfg, SETTINGS)

    def _run(self):
        if not self.files:
            messagebox.showinfo("Route Importer", "Add at least one drawing first."); return
        try:
            self._collect_cfg()
        except ValueError as e:
            messagebox.showerror("Settings", f"Check the settings: {e}"); return
        self.btn.state(["disabled"]); self.pb.start(12)
        proj = "".join(c if c.isalnum() or c in "-_" else "_" for c in self.project.get()) or "Route"
        args = (list(self.files), self.out.get(), proj, dict(self.cfg))

        def work():
            try:
                outs = run(*args, log=lambda s: self.q.put(("log", s)))
                self.q.put(("done", outs))
            except Exception:
                import traceback
                self.q.put(("log", traceback.format_exc()))
                self.q.put(("done", []))
        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self._say(val)
                else:
                    self.pb.stop(); self.btn.state(["!disabled"])
                    if val:
                        self._say("Done:\n  " + "\n  ".join(val) + "\nOpen the QA sheet first.")
                    else:
                        self._say("No output written - see messages above.")
        except queue.Empty:
            pass
        self.after(150, self._poll)


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
