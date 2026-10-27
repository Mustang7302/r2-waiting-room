"""Full-tree diff of one poll against the previous one.

Every leaf path is compared, so a field nobody thought to watch still shows up.
Arrays that carry stable ids are keyed by that id so a reordered list is not a
flood of changes. `__typename` is ignored. Paths on the noise list are still
recorded, just not alerted.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any

KEYED_ARRAYS = {
    "payments": "id",
    "items": "id",
    "tradeIns": "tradeInReferenceId",
    "options": "optionId",
    "vehicles": "id",
    "fulfillments": "fulfillmentId",
    "data": "id",  # vehicleOrders.data[]
}

DEFAULT_NOISE = [
    "offersByStore.*",
    "vehicleImages.*",
    "ENV.*",
    "*Enabled",
    "calculator.*",
    "priceBreakdown.*",
    "*.updatedAt",
    "userInfo.vehicles.*.updatedAt",
]

IGNORED_KEYS = {"__typename"}


@dataclass(frozen=True)
class Change:
    path: str
    old: Any
    new: Any
    kind: str  # added | removed | changed

    def to_dict(self) -> dict:
        return {"path": self.path, "old": self.old, "new": self.new, "kind": self.kind}


def _leaves(node: Any, prefix: str, out: dict[str, Any], parent_key: str = "") -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in IGNORED_KEYS:
                continue
            _leaves(v, f"{prefix}.{k}" if prefix else k, out, k)
    elif isinstance(node, list):
        key = KEYED_ARRAYS.get(parent_key)
        if key and all(isinstance(x, dict) and x.get(key) is not None for x in node):
            for x in node:
                _leaves(x, f"{prefix}[{x[key]}]", out, parent_key)
        else:
            for i, x in enumerate(node):
                _leaves(x, f"{prefix}[{i}]", out, parent_key)
    else:
        out[prefix] = node


def flatten(tree: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    _leaves(tree, "", out)
    return out


def diff(old: Any, new: Any) -> list[Change]:
    a, b = flatten(old), flatten(new)
    changes: list[Change] = []
    for path in sorted(set(a) | set(b)):
        if path not in a:
            changes.append(Change(path, None, b[path], "added"))
        elif path not in b:
            changes.append(Change(path, a[path], None, "removed"))
        elif a[path] != b[path]:
            changes.append(Change(path, a[path], b[path], "changed"))
    return changes


def _match(s: str, p: str) -> bool:
    if p.endswith(".*"):
        base = p[:-2]
        return s == base or s.startswith(base + ".") or s.startswith(base + "[")
    return fnmatch.fnmatchcase(s, p)


def is_noise(path: str, noise: list[str] | None = None) -> bool:
    patterns = DEFAULT_NOISE if noise is None else noise
    # Match against the path with and without the endpoint prefix so "offersByStore.*" catches "order.offersByStore[x].y".
    stripped = path.split(".", 1)[1] if "." in path else path
    return any(_match(path, p) or _match(stripped, p) for p in patterns)
