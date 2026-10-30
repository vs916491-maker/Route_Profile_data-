"""Command line:  python -m route_importer.cli drawing1.pdf drawing2.dxf -o out_folder -n Line_Name"""
import argparse
import os

from . import config
from .pipeline import run


def main():
    ap = argparse.ArgumentParser(description="GAD drawings (PDF/DXF/DWG) -> SEC route input Excel")
    ap.add_argument("files", nargs="+")
    ap.add_argument("-o", "--out", default=".")
    ap.add_argument("-n", "--name", default="Route")
    ap.add_argument("-s", "--settings", default=os.path.join(os.path.dirname(os.path.dirname(__file__)), "settings.json"))
    a = ap.parse_args()
    cfg = config.load(a.settings)
    run(a.files, a.out, a.name, cfg)


if __name__ == "__main__":
    main()
