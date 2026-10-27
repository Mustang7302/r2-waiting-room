"""The fulfillment ladder: nine rungs from preorder to delivered, each mapped to
raw Rivian fields. A rung never un-reaches. Rules evaluate top-down on every
poll; rungs reached out of the expected order are shown as reached, with the
date, rather than forced into sequence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

RUNG_NAMES = {
    1: "Preordered",
    2: "Invited to order",
    3: "Ordered",
    4: "In the queue",
    5: "Build matched",
    6: "VIN assigned",
    7: "Purchase opens",
    8: "Delivery set",
    9: "Delivered",
}

RUNG_FIELDS = {
    1: "state",
    2: "invitedToSelfConfigure / invitedToSelfConfirm (implied by an order)",
    3: "configurationStatus",
    4: "fulfillmentSummaryStatus",
    5: "vehicleId / configurationStatus",
    6: "vin / fulfillmentSummaryStatus",
    7: "isPreparingPurchase / steps.titleAndReg.locked",
    8: "delivery.status",
    9: "fulfillmentSummaryStatus",
}

# Names from earlier versions of rungs.json, so an upgrade never re-alerts or renumbers wrongly.
OLD_NAMES = {"Configured": "Ordered", "Confirm invite": None, "Reconfirm request": None}

# Rungs that are inferred rather than observed: reached without a date ("Complete") and never alerted on their own.
DATELESS = {2}


def _truthy(v: Any) -> bool:
    return bool(v) and v not in ("null", "None", "false", "False")


def _step_locked(f: dict, name: str) -> bool | None:
    s = (f.get("steps") or {}).get(name)
    return None if not s else s.get("locked")


@dataclass
class Rule:
    rung: int
    test: Callable[[dict], bool]
    backfill: Callable[[dict], str | None]  # ISO date for rungs reached before the first poll
    value: Callable[[dict], Any]


RULES: list[Rule] = [
    Rule(1, lambda f: f.get("state") == "ORDERED" or _truthy(f.get("orderDate")),
         lambda f: f.get("orderDate"), lambda f: f.get("state")),
    Rule(2, lambda f: _truthy(f.get("invitedToSelfConfigure")) or _truthy(f.get("invitedToSelfConfirm")) or _truthy(f.get("configurationStatus")),
         lambda f: None,
         lambda f: "invited" if (_truthy(f.get("invitedToSelfConfigure")) or _truthy(f.get("invitedToSelfConfirm"))) else "implied by order"),
    Rule(3, lambda f: _truthy(f.get("configurationStatus")),
         lambda f: f.get("configurationDate") or f.get("agreementSignedAt"), lambda f: f.get("configurationStatus")),
    Rule(4, lambda f: f.get("fulfillmentSummaryStatus") == "PENDING_FULFILLMENT",
         lambda f: None, lambda f: f.get("fulfillmentSummaryStatus")),
    Rule(5, lambda f: _truthy(f.get("vehicleId"))
         or (_truthy(f.get("configurationStatus")) and f.get("configurationStatus") != "SOFT_LOCKED")
         or _truthy((f.get("buildDates") or {}).get("plannedBuildDate")),
         lambda f: None,
         lambda f: ("vehicleId set" if _truthy(f.get("vehicleId")) else f.get("configurationStatus"))
         or (f.get("buildDates") or {}).get("plannedBuildDate")),
    Rule(6, lambda f: _truthy(f.get("vin")) or f.get("fulfillmentSummaryStatus") == "READY_FOR_FULFILLMENT",
         lambda f: None,
         lambda f: (("vin " + str(f["vin"])[:-3] + "•••") if _truthy(f.get("vin")) else f.get("fulfillmentSummaryStatus"))),
    Rule(7, lambda f: f.get("isPreparingPurchase") is True or _step_locked(f, "titleAndReg") is False,
         lambda f: None, lambda f: f.get("isPreparingPurchase")),
    Rule(8, lambda f: (f.get("delivery") or {}).get("status") in ("APPT_SCHEDULED", "DELIVERY_ACCEPTED")
         or _truthy((f.get("delivery") or {}).get("appointmentStart")),
         lambda f: None, lambda f: (f.get("delivery") or {}).get("status")),
    Rule(9, lambda f: f.get("fulfillmentSummaryStatus") == "VEHICLE_DELIVERED",
         lambda f: None, lambda f: f.get("fulfillmentSummaryStatus")),
]


def update_rungs(previous: list[dict] | None, facts: dict, now_iso: str, first_poll: bool) -> tuple[list[dict], list[int]]:
    """Merge the previous rungs.json with this poll's facts. Returns (rungs, newly_reached)."""
    prev: dict[str, dict] = {}
    for r in previous or []:
        name = OLD_NAMES.get(r.get("name"), r.get("name"))
        if name:
            prev[name] = r
    out: list[dict] = []
    newly: list[int] = []
    for rule in RULES:
        entry = {
            "rung": rule.rung,
            "name": RUNG_NAMES[rule.rung],
            "field": RUNG_FIELDS[rule.rung],
            "reached": False,
            "reached_at": None,
            "source": None,
            "value": None,
        }
        was = prev.get(RUNG_NAMES[rule.rung])
        if was and was.get("reached"):
            entry.update({k: was.get(k) for k in ("reached", "reached_at", "source")})
            entry["value"] = rule.value(facts) if rule.test(facts) else was.get("value")
        elif rule.test(facts):
            back = rule.backfill(facts)
            if rule.rung in DATELESS and not back:
                entry.update(reached=True, reached_at=None, source="implied")
            elif first_poll and back:
                entry.update(reached=True, reached_at=back, source="backfill")
            elif first_poll:
                entry.update(reached=True, reached_at=now_iso, source="first_poll")
            else:
                entry.update(reached=True, reached_at=now_iso, source="poll")
                newly.append(rule.rung)
            entry["value"] = rule.value(facts)
        out.append(entry)
    return out, newly


def current_rung(rungs: list[dict]) -> int:
    reached = [r["rung"] for r in rungs if r.get("reached")]
    return max(reached) if reached else 0


def next_signals(rungs: list[dict]) -> list[dict]:
    """The first unreached rungs, for the "watching for next" callout."""
    return [r for r in rungs if not r.get("reached")][:2]
