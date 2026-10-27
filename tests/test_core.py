"""Unit tests that need no network and no SESSION_KEY beyond what they set."""

import json
import os
from pathlib import Path

import pytest

from app import crypto, state
from app.diff import Change, diff, flatten, is_noise
from app.facts import build_facts
from app.ladder import current_rung, update_rungs
from app.sanitize import assert_clean, public_changes, public_snapshot

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def pending():
    return state.load_fixture("order.pending_fulfillment")


@pytest.fixture
def matched():
    return state.load_fixture("order.build_matched")


@pytest.fixture
def vin():
    return state.load_fixture("order.vin_assigned")


# ----- crypto -------------------------------------------------------------

def test_crypto_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_KEY", "correct horse battery staple 2026")
    p = tmp_path / "x.enc"
    crypto.write_enc(p, {"a": 1, "b": [1, 2]})
    assert crypto.read_enc(p) == {"a": 1, "b": [1, 2]}
    text = p.read_text()
    assert "correct" not in text and '"a"' not in text


def test_crypto_wrong_key_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_KEY", "one key that is long enough")
    p = tmp_path / "x.enc"
    crypto.write_enc(p, {"a": 1})
    monkeypatch.setenv("SESSION_KEY", "another key that is long enough")
    with pytest.raises(Exception):
        crypto.read_enc(p)


def test_crypto_short_key_rejected(monkeypatch):
    monkeypatch.setenv("SESSION_KEY", "short")
    with pytest.raises(RuntimeError):
        crypto.encrypt_json({"a": 1})


# ----- diff ---------------------------------------------------------------

def test_flatten_keys_arrays_by_id():
    tree = {"payments": [{"id": "p1", "amount": 1}, {"id": "p2", "amount": 2}]}
    flat = flatten(tree)
    assert flat["payments[p1].amount"] == 1 and flat["payments[p2].amount"] == 2


def test_diff_ignores_typename_and_reorder():
    a = {"items": [{"id": "x", "v": 1, "__typename": "A"}, {"id": "y", "v": 2}]}
    b = {"items": [{"id": "y", "v": 2}, {"id": "x", "v": 1, "__typename": "B"}]}
    assert diff(a, b) == []


def test_diff_detects_change_added_removed():
    out = diff({"a": 1, "b": 2}, {"a": 3, "c": 4})
    assert {(c.path, c.kind) for c in out} == {("a", "changed"), ("b", "removed"), ("c", "added")}


def test_noise_list():
    assert is_noise("order.offersByStore[x].name")
    assert is_noise("order.garageEditStepsEnabled")
    assert not is_noise("order.configurationStatus")
    assert is_noise("order.x", ["order.*"])


def test_diff_pending_to_matched(pending, matched):
    changed = {c.path: c for c in diff(pending, matched)}
    assert changed["order.configurationStatus"].new == "HARD_LOCKED"
    assert changed["order.vehicleId"].kind == "changed" and changed["order.vehicleId"].new == "REDACTED_VEHICLE_ID"
    assert changed["order.invitedToSelfConfirm"].new is True


# ----- facts + ladder -----------------------------------------------------

def test_facts_pending(pending):
    f = build_facts(pending)
    assert f["fulfillmentSummaryStatus"] == "PENDING_FULFILLMENT"
    assert f["configurationStatus"] == "SOFT_LOCKED"
    assert f["vehicle"]["build"] == "R2 Performance" and f["vehicle"]["modelYear"] == "2027"
    assert f["steps"]["deliveryMethod"]["locked"] is False
    assert f["steps"]["titleAndReg"]["locked"] is True
    assert f["steps"]["finance"]["status"] == "UNINITIATED"
    assert f["windowText"].startswith("in September")


def test_ladder_first_poll_backfills(pending):
    rungs, newly = update_rungs(None, build_facts(pending), "2026-10-27T18:00:00Z", first_poll=True)
    assert newly == []
    by = {r["rung"]: r for r in rungs}
    assert by[1]["reached"] and by[1]["source"] == "backfill" and by[1]["reached_at"].startswith("2026-03-08")
    assert by[2]["reached"] and by[2]["reached_at"] is None and by[2]["source"] == "implied"
    assert by[3]["reached"] and by[3]["reached_at"].startswith("2026-09-22")
    assert by[4]["reached"] and by[4]["source"] == "first_poll"
    assert not by[5]["reached"] and not by[6]["reached"]
    assert current_rung(rungs) == 4


def test_ladder_advances_and_never_unreaches(pending, matched, vin):
    r0, _ = update_rungs(None, build_facts(pending), "2026-10-27T18:00:00Z", first_poll=True)
    r1, new1 = update_rungs(r0, build_facts(matched), "2026-11-20T18:00:00Z", first_poll=False)
    assert set(new1) == {5}
    assert current_rung(r1) == 5
    r2, new2 = update_rungs(r1, build_facts(vin), "2026-12-10T18:00:00Z", first_poll=False)
    assert set(new2) == {6, 7}
    assert current_rung(r2) == 7
    # Going "backwards" (feed a pending payload again) keeps every rung reached.
    r3, new3 = update_rungs(r2, build_facts(pending), "2026-12-11T18:00:00Z", first_poll=False)
    assert new3 == [] and current_rung(r3) == 7
    assert {r["rung"] for r in r3 if r["reached"]} == {1, 2, 3, 4, 5, 6, 7}


def test_rung5_fires_on_unknown_config_status(pending):
    f = build_facts(pending)
    f["configurationStatus"] = "SOMETHING_NEW"
    rungs, _ = update_rungs(None, f, "2026-10-27T18:00:00Z", first_poll=True)
    assert {r["rung"]: r["reached"] for r in rungs}[5] is True


def test_rungs_migrate_from_old_nine_rung_file(pending, matched):
    old = [
        {"rung": 1, "name": "Preordered", "reached": True, "reached_at": "2026-03-08", "source": "backfill"},
        {"rung": 2, "name": "Configured", "reached": True, "reached_at": "2026-09-22", "source": "backfill"},
        {"rung": 3, "name": "In the queue", "reached": True, "reached_at": "2026-10-27T01:00:00Z", "source": "first_poll"},
        {"rung": 4, "name": "Invited to order", "reached": False},
        {"rung": 5, "name": "Build matched", "reached": False},
    ]
    rungs, newly = update_rungs(old, build_facts(pending), "2026-10-28T00:00:00Z", first_poll=False)
    assert newly == [] and current_rung(rungs) == 4  # the implied invite rung never alerts
    assert [r["name"] for r in rungs][:4] == ["Preordered", "Invited to order", "Ordered", "In the queue"]
    assert rungs[1]["reached"] and rungs[1]["reached_at"] is None
    assert rungs[2]["reached_at"] == "2026-09-22"
    rungs2, newly2 = update_rungs(rungs, build_facts(matched), "2026-11-20T00:00:00Z", first_poll=False)
    assert newly2 == [5] and rungs2[4]["name"] == "Build matched"


# ----- sanitize -----------------------------------------------------------

FORBIDDEN_VALUES = ["Roslyn", "Glenside", "19038", "tps14334", "Shelton", "+1832", "7564"]


def test_snapshot_has_no_pii(pending, vin):
    for raw in (pending, vin):
        snap = public_snapshot(build_facts(raw))
        text = json.dumps(snap)
        assert_clean(snap)
        for bad in FORBIDDEN_VALUES + ["REDACTED"]:
            assert bad not in text, bad
    snap = public_snapshot(build_facts(vin))
    assert snap["vin"] == "7PDSGABA0SN000•••"
    assert snap["vehicleId"] == "set"
    assert public_snapshot(build_facts(vin), full_vin=True)["vin"] == "7PDSGABA0SN000000"


def test_changes_allowlist(pending, vin):
    changes = diff(pending, vin)
    pub = public_changes(changes)
    paths = {c["path"] for c in pub}
    assert "order.configurationStatus" in paths
    assert "order.vin" in paths and next(c for c in pub if c["path"] == "order.vin")["new"].endswith("\u2022\u2022\u2022") and not next(c for c in pub if c["path"] == "order.vin")["new"].endswith("000")
    assert "order.vehicleId" in paths and next(c for c in pub if c["path"] == "order.vehicleId")["new"] == "set"
    assert "delivery.status" in paths
    assert "delivery.deliveryAddress.city" not in paths
    assert not any("payments" in p or "billingAddress" in p or "orderEmail" in p for p in paths)
    assert_clean(pub)


def test_assert_clean_catches_pii():
    with pytest.raises(RuntimeError):
        assert_clean({"shippingAddress": {"line1": "x"}})
    with pytest.raises(RuntimeError):
        assert_clean({"orderEmail": "someone@example.com"})


# ----- fixtures -----------------------------------------------------------

def test_fixture_files_are_redacted():
    for p in (ROOT / "fixtures").glob("*.json"):
        text = p.read_text(encoding="utf-8")
        for bad in FORBIDDEN_VALUES:
            assert bad not in text, f"{p.name} contains {bad}"


# ----- adaptive queries ---------------------------------------------------

def test_rejected_fields_are_stripped_and_rest_kept():
    from app.rivian import queries as Q

    err = ('order: Cannot query field "garageLeaseStatus" on type "Order".; '
           'Cannot query field "consumerStatuses" on type "Order".')
    rej = Q.rejected_fields(err)
    assert rej == {"garageLeaseStatus", "consumerStatuses"}
    q = Q.strip_fields(Q.ORDER_EXT["query"], rej)
    assert "garageLeaseStatus" not in q and "consumerStatuses" not in q and "isPreparingPurchase" not in q
    for keep in ("vehicleId", "configurationStatus", "invitedToSelfConfirm", "payments {", "options {"):
        assert keep in q
    assert q.count("{") == q.count("}")
