"""Write settings.json from workflow inputs, so owners can change options from the Actions tab.

    python -m app.settings_cli --heartbeat on|off|keep --full-vin show|hide|keep
"""

from __future__ import annotations

import argparse
import sys

from . import state


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--heartbeat", choices=["on", "off", "keep"], default="keep")
    p.add_argument("--full-vin", choices=["show", "hide", "keep"], default="keep")
    a = p.parse_args(argv)
    cfg = state.read_json(state.SETTINGS, {}) or {}
    if a.heartbeat != "keep":
        cfg["heartbeat"] = a.heartbeat == "on"
    if a.full_vin != "keep":
        cfg["full_vin"] = a.full_vin == "show"
    state.write_json(state.SETTINGS, cfg)
    print("settings.json:", cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
