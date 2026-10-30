"""Default settings. A settings.json next to the tool overrides any of these."""
import json
import os

DEFAULTS = {
    # stations
    "dwell_s": 30,
    "terminal_dwell_s": 180,
    # speeds
    "line_speed_kmh": 80,          # used where the drawing gives no restriction
    "speed_round_down_kmh": 5,     # calculated curve speeds rounded down to this step
    # cant / curve speed  (V = sqrt(127 R (Ca + Cd) / G))
    "cant_gauge_mm": 1500,         # centre-to-centre of rail heads (1435 mm track -> ~1500)
    "cant_deficiency_mm": 100,
    "max_cant_mm": 125,
    "cant_ramp_1_in": 720,         # default run-out ramp when the drawing gives only transition length
    # curves for SEC: 'arc' | 'arc_plus_half_transitions' | 'full' (TS..ST)
    "curve_extent_for_sec": "arc_plus_half_transitions",
    # reading
    "ocr": "auto",                 # auto | always | never
    "ocr_dpi": 300,
    # output
    "sec_comment_column": True,    # extra 'comment' column on Gradients / Curves / SpeedLimits
    "row_keywords": {},            # extra band-label words, e.g. {"rail": ["PROPOSED RL"]}
}


def load(path: str | None = None) -> dict:
    cfg = dict(DEFAULTS)
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


def save(cfg: dict, path: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
