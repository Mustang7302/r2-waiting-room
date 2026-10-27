"""One poll: fetch -> diff -> ladder -> publish -> notify.

    python -m app.poller            # live, needs state/session.enc and SESSION_KEY
    python -m app.poller --mock     # fixtures only, no Rivian calls, no SESSION_KEY needed
    python -m app.poller --mock --fixture build_matched

Exit codes: 0 ok, 2 session expired (so the workflow goes red and GitHub emails the owner),
1 anything else. meta.json is written on every path so the dashboard can explain itself.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import Any

from . import crypto, state
from .diff import Change, diff, is_noise
from .facts import build_facts
from .ladder import RUNG_NAMES, current_rung, next_signals, update_rungs
from .notify import format_changes, send
from .rivian import RivianAuthError, RivianClient, RivianError
from .rivian.client import Session
from .sanitize import assert_clean, public_changes, public_snapshot

log = logging.getLogger("r2wr")

ENDPOINTS = ["vehicleOrders", "order", "transactionStatus", "delivery", "userInfo"]


def fetch_live(order_id_override: str | None) -> tuple[dict[str, Any], dict[str, str]]:
    """Returns (raw, errors). Raises RivianAuthError only when the session itself is dead."""
    sess = crypto.read_enc(state.SESSION_ENC)
    if not sess:
        raise RivianAuthError("state/session.enc is missing; run the Login workflow")
    client = RivianClient(Session.from_dict(sess))
    raw: dict[str, Any] = {}
    errors: dict[str, str] = {}

    # vehicleOrders is the auth canary: if it fails with an auth error the session is gone.
    orders = client.vehicle_orders()
    raw["vehicleOrders"] = orders
    vehicle_orders = [o for o in orders if o.get("state") not in ("CANCELLED", "CANCELED")] or orders
    order_id = order_id_override or (vehicle_orders[0]["id"] if vehicle_orders else None)
    if not order_id:
        errors["order"] = "no vehicle order found on this account"
        raw.update(order={}, transactionStatus={}, delivery=None, userInfo={})
        return raw, errors

    for name, call in (
        ("order", lambda: client.order(order_id)),
        ("transactionStatus", lambda: client.transaction_status(order_id)),
        ("delivery", lambda: client.delivery(order_id)),
        ("userInfo", lambda: client.user_info()),
    ):
        try:
            result = call()
            if isinstance(result, tuple):
                result, note = result
                if note:
                    errors[f"{name}.note"] = note
            raw[name] = result
        except RivianAuthError:
            raise
        except (RivianError, KeyError, TypeError) as e:
            errors[name] = str(e)
            raw[name] = None
    if isinstance(raw.get("order"), dict) and "id" not in raw["order"]:
        raw["order"]["id"] = order_id
    return raw, errors


def run(mock: bool, fixture: str, notify: bool = True) -> int:
    t0 = time.time()
    now = state.now_iso()
    cfg = state.settings()
    meta_prev = state.read_json(state.META, {}) or {}
    first_poll_at = meta_prev.get("first_poll_at") or now
    errors: dict[str, str] = {}

    try:
        if mock:
            raw = state.load_fixture(fixture)
            prev = state.read_json(state.STATE_DIR / "mock_prev.json")
        else:
            raw, errors = fetch_live(cfg.get("order_id"))
            prev = crypto.read_enc(state.RAW_ENC)
    except RivianAuthError as e:
        log.error("session expired: %s", e)
        meta = dict(meta_prev, last_poll_at=now, ok=False, auth_expired=True, errors={"auth": str(e)}, mode="live",
                    first_poll_at=first_poll_at)
        state.write_json(state.META, meta)
        if notify:
            send("Session expired, polling paused",
                 f"Rivian returned: {e}\nRun the Login workflow again (start, then finish with the code).",
                 priority="high", tags="rotating_light")
        return 2
    except Exception as e:  # noqa: BLE001
        log.exception("poll failed")
        meta = dict(meta_prev, last_poll_at=now, ok=False, errors={"poll": str(e)}, first_poll_at=first_poll_at)
        state.write_json(state.META, meta)
        return 1

    # Diff against the previous raw snapshot (decrypted in live mode, plain file in mock mode).
    first_poll = prev is None
    changes: list[Change] = [] if first_poll else diff(prev, raw)
    noise = cfg.get("noise")
    alertable = [c for c in changes if not is_noise(c.path, noise)]

    facts = build_facts(raw)
    rungs_prev = state.read_json(state.RUNGS, [])
    rungs, newly = update_rungs(rungs_prev, facts, now, first_poll)
    rung_now = current_rung(rungs)

    # Publish (public, sanitized).
    snapshot = public_snapshot(facts, full_vin=bool(cfg.get("full_vin")))
    pub_changes = public_changes(alertable, full_vin=bool(cfg.get("full_vin")))
    for pc in pub_changes:
        pc["seen_at"] = now
    log_prev = state.read_json(state.CHANGES, []) or []
    change_log = (pub_changes + log_prev)[: state.MAX_CHANGES]

    meta = {
        "mode": "mock" if mock else "live",
        "fixture": fixture if mock else None,
        "first_poll_at": first_poll_at,
        "last_poll_at": now,
        "ok": True,
        "auth_expired": False,
        "duration_s": round(time.time() - t0, 2),
        "endpoints": {k: ("ok" if raw.get(k) not in (None, {}) or k == "delivery" else "empty") for k in ENDPOINTS},
        "errors": errors,
        "fields_watched": sum(1 for _ in _iter_leaves(raw)),
        "changes_this_poll": len(changes),
        "alertable_this_poll": len(alertable),
        "noise_this_poll": len(changes) - len(alertable),
        "rung": rung_now,
        "next": [{"rung": r["rung"], "name": r["name"], "field": r["field"]} for r in next_signals(rungs)],
        "session_created_at": _session_age(),
        "poll_interval_min": int(os.environ.get("POLL_INTERVAL_MIN", "240")),
    }

    for obj in (snapshot, change_log, rungs, meta):
        assert_clean(obj)
    state.write_json(state.LATEST, snapshot)
    state.write_json(state.RUNGS, rungs)
    state.write_json(state.CHANGES, change_log)
    state.write_json(state.META, meta)

    if mock:
        state.write_json(state.STATE_DIR / "mock_prev.json", raw)
    else:
        crypto.write_enc(state.RAW_ENC, raw)

    log.info("rung %s; %d changes (%d alertable); errors=%s", rung_now, len(changes), len(alertable), errors or "none")
    if notify and (pub_changes or newly):
        headline, body, priority = format_changes(pub_changes, rung_now, newly, RUNG_NAMES)
        send(headline, body, priority=priority, tags="truck" if newly else "")
    return 0


def _iter_leaves(obj: Any):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_leaves(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_leaves(v)
    else:
        yield obj


def _session_age() -> str | None:
    try:
        sess = crypto.read_enc(state.SESSION_ENC)
    except Exception:  # noqa: BLE001
        return None
    if not sess or not sess.get("created_at"):
        return None
    from datetime import datetime, timezone

    return datetime.fromtimestamp(sess["created_at"], tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Poll Rivian once and publish the dashboard data.")
    p.add_argument("--mock", action="store_true", help="use fixtures instead of calling Rivian")
    p.add_argument("--fixture", default=os.environ.get("MOCK_FIXTURE", "order.pending_fulfillment"))
    p.add_argument("--no-notify", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    mock = a.mock or os.environ.get("MOCK_RIVIAN") == "1"
    return run(mock=mock, fixture=a.fixture, notify=not a.no_notify)


if __name__ == "__main__":
    sys.exit(main())
