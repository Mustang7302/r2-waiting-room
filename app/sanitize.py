"""What is allowed into docs/data/ (public, served by GitHub Pages).

Two layers:
1. `public_snapshot(facts)` builds latest.json from the facts dict only. Facts never
   carry names, addresses, phone, email, payments, or amounts, so this is an allowlist
   by construction. VIN has its last 3 characters hidden unless settings opt into the full VIN.
2. `public_changes(changes)` keeps only change rows whose path matches an allowlisted
   pattern, with values transformed the same way (VIN with its last 3 hidden, ids masked).
"""

from __future__ import annotations

import re
from typing import Any

from .diff import Change

# Regexes on the dotted change path, e.g. "order.configurationStatus".
ALLOWED_PATHS = [
    r"^(order|vehicleOrders\[[^\]]+\])\.(state|configurationStatus|orderConfirmationType|fulfillmentSummaryStatus|invitedToSelfConfirm|invitedToSelfConfigure|orderDate|orderCancelDate|garageLeaseStatus|garageEstimatedInvitedToSelfConfigureDate|garageInitialAgreementSignedAt|t2dRefreshEnabled)$",
    r"^order\.vehicleId$",
    r"^order\.vin$",
    r"^order\.vehicle\.(vehicleId|vin|modelYear|model|make)$",
    r"^order\.items\[[^\]]+\]\.configuration\.(configurationDate|version)$",
    r"^order\.items\[[^\]]+\]\.configuration\.options\[[^\]]+\]\.(optionId|optionName|groupId|groupName)$",
    r"^order\.consumerStatuses\.(isPreparingPurchase|isConsumerFlowComplete)$",
    r"^order\.consumerStatuses\.steps\.[A-Za-z]+\.(displayOrder|hide|locked|error|notStarted|inProgress|completed|allowEditPostCompletion)$",
    r"^order\.consumerStatuses\.steps\.[A-Za-z]+\.(dependsOnConsumerCompletion|incompleteConsumerDependencies)\[\d+\]$",
    r"^order\.consumerStatuses\.[A-Za-z]+\.(status|consumerStatus|consumerSubStatus)$",
    r"^order\.garageSurvey\.[A-Za-z_]+$",
    r"^transactionStatus\.[A-Za-z]+\.sourceStatus\.status$",
    r"^transactionStatus\.[A-Za-z]+\.consumerStatus\.(displayOrder|current|complete|locked|inProgress|notStarted|error)$",
    r"^delivery\.(status|carrier|vehicleVIN)$",
    r"^delivery\.appointmentDetails\.(startDateTime|endDateTime|timeZone)$",
    r"^userInfo\.vehicles\[[^\]]+\]\.(state|vin|roles\[\d+\])$",
    r"^userInfo\.vehicles\[[^\]]+\]\.vehicle\.(vin|modelYear|make|model|expectedBuildDate|plannedBuildDate|expectedGeneralAssemblyStartDate|actualGeneralAssemblyDate|otaEarlyAccessStatus)$",
    r"^userInfo\.vehicles\[[^\]]+\]\.vehicle\.mobileConfiguration\.[A-Za-z]+\.(optionId|optionName)$",
]
_ALLOWED = [re.compile(p) for p in ALLOWED_PATHS]

_VIN_PATH = re.compile(r"(^|\.)(vin|vehicleVIN)$")
_ID_PATH = re.compile(r"(^|\.)(vehicleId|id)$")
_BRACKET_ID = re.compile(r"\[([^\]]+)\]")


def mask_vin(v: Any, full: bool = False) -> Any:
    """Default hides the last 3 characters of the serial; showing the last 4 alone would still identify the vehicle."""
    if not isinstance(v, str) or not v:
        return v
    return v if full else v[:-3] + "•••"


def mask_id(v: Any) -> Any:
    if v is None:
        return None
    return "set"


def _mask_path(path: str) -> str:
    # Replace long ids inside brackets (payment ids, item ids, order ids) with a short stable hash so paths stay opaque.
    import hashlib

    def rep(m):
        key = m.group(1)
        if len(key) <= 12 and "-" not in key:
            return f"[{key}]"
        return f"[#{hashlib.sha1(key.encode()).hexdigest()[:6]}]"
    return _BRACKET_ID.sub(rep, path)


def public_change(c: Change, full_vin: bool = False) -> dict | None:
    if not any(p.match(c.path) for p in _ALLOWED):
        return None
    if c.old is None and c.new is None:  # a field that appeared or vanished while empty says nothing
        return None
    old, new = c.old, c.new
    if _VIN_PATH.search(c.path):
        old, new = mask_vin(old, full_vin), mask_vin(new, full_vin)
    elif _ID_PATH.search(c.path):
        old, new = mask_id(old), mask_id(new)
    return {"path": _mask_path(c.path), "old": old, "new": new, "kind": c.kind}


def public_changes(changes: list[Change], full_vin: bool = False) -> list[dict]:
    out = []
    for c in changes:
        pc = public_change(c, full_vin)
        if pc:
            out.append(pc)
    return out


def public_snapshot(facts: dict, full_vin: bool = False) -> dict[str, Any]:
    """latest.json: everything the dashboard needs, nothing personal."""
    steps = []
    for name, s in (facts.get("steps") or {}).items():
        steps.append({
            "key": name,
            "label": s.get("label"),
            "displayOrder": s.get("displayOrder"),
            "locked": s.get("locked"),
            "hide": s.get("hide"),
            "notStarted": s.get("notStarted"),
            "inProgress": s.get("inProgress"),
            "completed": s.get("completed"),
            "status": s.get("status"),
        })
    steps.sort(key=lambda s: (s["displayOrder"] is None, s["displayOrder"] or 0))

    return {
        "orderIdLast4": facts.get("orderIdLast4"),
        "state": facts.get("state"),
        "configurationStatus": facts.get("configurationStatus"),
        "fulfillmentSummaryStatus": facts.get("fulfillmentSummaryStatus"),
        "orderConfirmationType": facts.get("orderConfirmationType"),
        "invitedToSelfConfirm": facts.get("invitedToSelfConfirm"),
        "invitedToSelfConfigure": facts.get("invitedToSelfConfigure"),
        "orderDate": facts.get("orderDate"),
        "configurationDate": facts.get("configurationDate"),
        "agreementSignedAt": facts.get("agreementSignedAt"),
        "windowText": facts.get("windowText"),
        "leaseStatus": facts.get("leaseStatus"),
        "vehicleId": mask_id(facts.get("vehicleId")),
        "vin": mask_vin(facts.get("vin"), full_vin),
        "isPreparingPurchase": facts.get("isPreparingPurchase"),
        "isConsumerFlowComplete": facts.get("isConsumerFlowComplete"),
        "steps": steps,
        "delivery": facts.get("delivery"),
        "vehicle": facts.get("vehicle"),
        "buildDates": facts.get("buildDates"),
    }


FORBIDDEN_MARKERS = (
    "firstName", "lastName", "line1", "postalCode", "phone", "orderEmail", "@", "last4", "card", "bank",
    "paidTotal", "remainingTotal", "amount", "userId", "addressLine1",
)


def assert_clean(obj: Any) -> None:
    """Belt and braces: refuse to publish anything that smells like PII. Raises on a hit."""
    import json

    text = json.dumps(obj)
    hits = [m for m in FORBIDDEN_MARKERS if f'"{m}"' in text or (m == "@" and "@" in text)]
    if hits:
        raise RuntimeError(f"refusing to publish: forbidden markers present {hits}")
