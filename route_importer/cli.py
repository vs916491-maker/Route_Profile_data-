"""Command line:  python -m route_importer.cli drawing1.pdf drawing2.dxf -o out_folder -n Line_Name
                [-r route_list.txt] [-d DN]"""
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
    ap.add_argument("-r", "--route", help="route list .txt (stations in running order, 'NAME = chainage' allowed)")
    ap.add_argument("-d", "--direction", choices=["UP", "DN"], help="line for the single route (default from settings)")
    a = ap.parse_args()
    cfg = config.load(a.settings)
    if a.route:
        with open(a.route, encoding="utf-8") as f:
            cfg["route_stations"] = f.read()
    if a.direction:
        cfg["cad_direction"] = a.direction
    run(a.files, a.out, a.name, cfg)


if __name__ == "__main__":
    main()
