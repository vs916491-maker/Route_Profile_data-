"""Route Importer launcher - double-click this file (no .bat needed).

.pyw opens with Python's windowless launcher (pythonw), so no black console window.
If double-click does nothing, right-click -> Open with -> Python, or open it in
IDLE / VS Code and Run (F5).
"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

NEEDED = {"numpy": "numpy", "openpyxl": "openpyxl", "pypdfium2": "pypdfium2",
          "ezdxf": "ezdxf", "rapidocr_onnxruntime": "rapidocr_onnxruntime"}


def _message(title, text):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk(); root.withdraw()
        messagebox.showerror(title, text)
        root.destroy()
    except Exception:
        print(title + "\n" + text)
        input("Press Enter to close")


def main():
    try:
        import tkinter  # noqa: F401
    except ImportError:
        _message("Route Importer", "This Python has no tkinter (window library).\n"
                 "Install Python from python.org with the 'tcl/tk and IDLE' option ticked.")
        return
    missing = []
    for mod, pkg in NEEDED.items():
        try:
            __import__(mod)
        except Exception:
            missing.append(pkg)
    if missing:
        _message("Route Importer - packages missing",
                 "These Python packages are not installed:\n  " + "\n  ".join(missing) +
                 "\n\nDouble-click install_packages.py (once), then start Route_Importer.pyw again.")
        return
    try:
        from route_importer.gui import main as gui_main
        gui_main()
    except Exception:
        log = os.path.join(HERE, "route_importer_error.log")
        with open(log, "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        _message("Route Importer - error", f"The tool stopped with an error.\nDetails saved in:\n{log}")


if __name__ == "__main__":
    main()
