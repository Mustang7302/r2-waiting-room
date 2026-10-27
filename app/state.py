"""Where things live in the repo, plus the mock-mode loader.

state/   encrypted, committed: session.enc, pending.enc, raw.enc
docs/data/   public JSON the Pages dashboard reads: latest.json, rungs.json, changes.json, meta.json
fixtures/    redacted sample payloads for mock mode and tests
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("R2WR_ROOT", Path(__file__).resolve().parent.parent))
STATE_DIR = ROOT / "state"
DATA_DIR = ROOT / "docs" / "data"
FIXTURES_DIR = ROOT / "fixtures"

SESSION_ENC = STATE_DIR / "session.enc"
PENDING_ENC = STATE_DIR / "pending.enc"
RAW_ENC = STATE_DIR / "raw.enc"

LATEST = DATA_DIR / "latest.json"
RUNGS = DATA_DIR / "rungs.json"
CHANGES = DATA_DIR / "changes.json"
META = DATA_DIR / "meta.json"
SETTINGS = ROOT / "settings.json"

MAX_CHANGES = 500


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, sort_keys=False)
        f.write("\n")


def settings() -> dict:
    """settings.json is optional; every key has a default."""
    s = read_json(SETTINGS, {}) or {}
    s.setdefault("full_vin", False)
    s.setdefault("heartbeat", True)  # a quiet "checked, no change" push after every poll
    s.setdefault("noise", None)  # None = DEFAULT_NOISE from diff.py
    s.setdefault("order_id", None)  # None = first vehicle order from vehicleOrders
    return s


def load_fixture(name: str) -> dict[str, Any]:
    """Turn a fixture file into the per-endpoint `raw` shape the poller uses."""
    path = FIXTURES_DIR / (name if name.endswith(".json") else f"{name}.json")
    fx = read_json(path)
    if fx is None:
        raise FileNotFoundError(f"fixture not found: {path}")
    order = fx.get("order") or {}
    if "id" not in order and fx.get("vehicleOrders_derived"):
        order = dict(order, id=fx["vehicleOrders_derived"].get("id"))
    raw: dict[str, Any] = {
        "order": order,
        "vehicleOrders": [fx["vehicleOrders_derived"]] if fx.get("vehicleOrders_derived") else [],
        "transactionStatus": fx.get("transactionStatus") or _tx_from_order(order),
        "delivery": (fx.get("deliveryData") or {}).get("delivery") if "deliveryData" in fx else fx.get("delivery"),
        "userInfo": fx.get("userInfo") or {"vehicles": []},
    }
    return raw


def _tx_from_order(order: dict) -> dict:
    """Synthesize the t2d transactionStatus shape from an order's consumerStatuses (fixtures only)."""
    cs = order.get("consumerStatuses") or {}
    out = {}
    for name, flags in (cs.get("steps") or {}).items():
        if not isinstance(flags, dict):
            continue
        sub = cs.get(name) if isinstance(cs.get(name), dict) else {}
        out[name] = {
            "sourceStatus": {"status": sub.get("status"), "details": sub.get("details")},
            "consumerStatus": {
                "displayOrder": flags.get("displayOrder"),
                "current": False,
                "complete": flags.get("completed"),
                "locked": flags.get("locked"),
                "inProgress": flags.get("inProgress"),
                "notStarted": flags.get("notStarted"),
                "error": flags.get("error"),
            },
        }
    return out
