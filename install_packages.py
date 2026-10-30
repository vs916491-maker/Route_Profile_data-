"""One-time install of the Python packages for Route Importer (replaces install.bat).

Double-click this file, or open it in IDLE / VS Code and Run (F5).
- Installs for your Windows user only (--user): no admin rights needed.
- If a 'wheels' folder is next to this file (made by make_offline_bundle.py on a
  PC with internet), it installs from there with no internet at all.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REQ = os.path.join(HERE, "requirements.txt")
WHEELS = os.path.join(HERE, "wheels")


def run(args):
    print(">", " ".join(args))
    return subprocess.call(args)


def main():
    print(f"Python {sys.version.split()[0]} at {sys.executable}\n")
    if sys.version_info < (3, 9):
        print("Python 3.9 or newer is needed.")
        return
    cmd = [sys.executable, "-m", "pip", "install", "--user", "-r", REQ]
    if os.path.isdir(WHEELS):
        print("Installing from the local 'wheels' folder (offline)...\n")
        cmd += ["--no-index", "--find-links", WHEELS]
    else:
        print("Installing from the internet (one time only)...\n")
    code = run(cmd)
    print()
    ok = True
    for mod in ("numpy", "openpyxl", "pypdfium2", "ezdxf", "rapidocr_onnxruntime", "tkinter"):
        try:
            __import__(mod)
            print(f"  OK       {mod}")
        except Exception as e:
            ok = False
            print(f"  MISSING  {mod}  ({e.__class__.__name__}: {e})")
    print()
    if ok:
        print("All set. Start the tool by double-clicking Route_Importer.pyw")
    else:
        print("Some packages did not install (pip exit code %s)." % code)
        print("If the office network blocks pip: run make_offline_bundle.py on a PC with internet,")
        print("copy the 'wheels' folder here, and run this file again.")
        print("If a package installs but will not import, IT Application Control may be blocking it.")


if __name__ == "__main__":
    try:
        main()
    finally:
        input("\nPress Enter to close")
