"""Run on a PC WITH internet (same Python version and Windows 64-bit as the work laptop).
Downloads every package into .\\wheels so install_packages.py works with no internet."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

if __name__ == "__main__":
    try:
        subprocess.call([sys.executable, "-m", "pip", "download", "-r",
                         os.path.join(HERE, "requirements.txt"), "-d", os.path.join(HERE, "wheels")])
        print("\nCopy the whole folder (with 'wheels') to the work laptop and run install_packages.py")
    finally:
        input("\nPress Enter to close")
