"""Turn the raw per-endpoint responses into one flat `facts` dict the ladder, the
watch list, and the dashboard all read from.

Precedence when the same fact appears in more than one endpoint:
order (extended) > vehicleOrders > transactionStatus > delivery > userInfo.
"""

from __future__ import annotations

from typing import Any

STEP_NAMES = [
    "titleAndReg", "taxCredit", "tradeIn", "deliveryMethod", "rivianCare", "finance", "insurance",
    "documentUpload", "contracts", "financeAgreement", "payment", "deliveryScheduling",
]

STEP_LABELS = {
    "titleAndReg": "Title and reg",
    "taxCredit": "Tax credit",
    "tradeIn": "Trade-in",
    "deliveryMethod": "Delivery method",
    "rivianCare": "Rivian Care",
    "finance": "Finance",
    "insurance": "Insurance",
    "documentUpload": "Documents",
    "contracts": "Contracts",
    "financeAgreement": "Finance agreement",
    "payment": "Payment",
    "deliveryScheduling": "Delivery scheduling",
    "delivery": "Delivery",
}

OPTION_GROUPS_SHOWN = ["BLD", "MODEL_YEAR", "BAT", "MOT", "DTN", "EXP", "INT", "WHL", "LPKG", "TOW", "AUTO"]


def _g(d: Any, *path: str, default=None):
    cur = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur or cur[p] is None:
            return default
        cur = cur[p]
    return cur


def _vehicle_item(order: dict | None) -> dict | None:
    for it in (order or {}).get("items") or []:
        if (it.get("type") == "VEHICLE") or (it.get("sku") in ("R2", "R1T", "R1S", "R3")):
            return it
    return None


def build_facts(raw: dict[str, Any]) -> dict[str, Any]:
    order: dict = raw.get("order") or {}
    vo_list: list = raw.get("vehicleOrders") or []
    vo: dict = vo_list[0] if vo_list else {}
    tx: dict = raw.get("transactionStatus") or {}
    delivery: dict = raw.get("delivery") or {}
    user: dict = raw.get("userInfo") or {}

    cs = order.get("consumerStatuses") or {}
    veh_item = _vehicle_item(order)
    cfg = (veh_item or {}).get("configuration") or {}
    options = {o.get("groupId"): o.get("optionName") for o in cfg.get("options") or [] if isinstance(o, dict)}

    # Steps: flags from order.consumerStatuses.steps, status strings from the sub-status objects,
    # falling back to transactionStatus (sourceStatus/consumerStatus) when the extended order query is unavailable.
    steps: dict[str, dict] = {}
    for name in STEP_NAMES + ["delivery"]:
        flags = _g(cs, "steps", name) or {}
        sub = cs.get(name) if isinstance(cs.get(name), dict) else {}
        t = tx.get(name) if isinstance(tx.get(name), dict) else {}
        tflags = t.get("consumerStatus") or {}
        tsrc = t.get("sourceStatus") or {}
        if not flags and not sub and not t:
            continue
        steps[name] = {
            "label": STEP_LABELS.get(name, name),
            "displayOrder": flags.get("displayOrder", tflags.get("displayOrder")),
            "locked": flags.get("locked", tflags.get("locked")),
            "hide": flags.get("hide"),
            "notStarted": flags.get("notStarted", tflags.get("notStarted")),
            "inProgress": flags.get("inProgress", tflags.get("inProgress")),
            "completed": flags.get("completed", tflags.get("complete")),
            "status": sub.get("status") if sub else tsrc.get("status"),
        }

    user_vehicle = None
    for v in user.get("vehicles") or []:
        if isinstance(v, dict) and v.get("vehicle"):
            user_vehicle = v["vehicle"]
            break

    appt = delivery.get("appointmentDetails") or {}
    order_id = str(order.get("id") or vo.get("id") or "")

    return {
        "orderIdLast4": order_id[-4:] if order_id else None,
        "state": order.get("state") or vo.get("state"),
        "configurationStatus": order.get("configurationStatus") or vo.get("configurationStatus"),
        "fulfillmentSummaryStatus": order.get("fulfillmentSummaryStatus") or vo.get("fulfillmentSummaryStatus"),
        "orderConfirmationType": order.get("orderConfirmationType"),
        "invitedToSelfConfirm": order.get("invitedToSelfConfirm"),
        "invitedToSelfConfigure": order.get("invitedToSelfConfigure"),
        "orderDate": order.get("orderDate") or vo.get("orderDate"),
        "configurationDate": cfg.get("configurationDate"),
        "agreementSignedAt": order.get("garageInitialAgreementSignedAt"),
        "windowText": order.get("garageEstimatedInvitedToSelfConfigureDate"),
        "leaseStatus": order.get("garageLeaseStatus"),
        "vehicleId": order.get("vehicleId") or _g(order, "vehicle", "vehicleId"),
        "vin": order.get("vin") or _g(order, "vehicle", "vin") or delivery.get("vehicleVIN"),
        "isPreparingPurchase": cs.get("isPreparingPurchase"),
        "isConsumerFlowComplete": cs.get("isConsumerFlowComplete", _g(vo, "consumerStatuses", "isConsumerFlowComplete")),
        "steps": steps,
        "delivery": {
            "status": delivery.get("status"),
            "carrier": delivery.get("carrier"),
            "appointmentStart": appt.get("startDateTime"),
            "appointmentEnd": appt.get("endDateTime"),
            "timeZone": appt.get("timeZone"),
        } if delivery else {"status": None, "carrier": None, "appointmentStart": None, "appointmentEnd": None, "timeZone": None},
        "vehicle": {
            "model": (veh_item or {}).get("sku") or _g(order, "vehicle", "model") or (user_vehicle or {}).get("model"),
            "modelYear": options.get("MODEL_YEAR") or _g(order, "vehicle", "modelYear") or (user_vehicle or {}).get("modelYear"),
            "build": options.get("BLD"),
            "exterior": options.get("EXP"),
            "interior": options.get("INT"),
            "wheels": options.get("WHL"),
            "battery": options.get("BAT"),
            "motor": options.get("MOT"),
            "packages": [options[g] for g in ("LPKG", "TOW", "AUTO") if options.get(g)],
            "configVersion": cfg.get("version"),
        },
        "buildDates": {
            "expectedBuildDate": (user_vehicle or {}).get("expectedBuildDate"),
            "plannedBuildDate": (user_vehicle or {}).get("plannedBuildDate"),
            "expectedGeneralAssemblyStartDate": (user_vehicle or {}).get("expectedGeneralAssemblyStartDate"),
            "actualGeneralAssemblyDate": (user_vehicle or {}).get("actualGeneralAssemblyDate"),
        },
    }
