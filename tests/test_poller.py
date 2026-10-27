"""End-to-end in mock mode against a temporary repo root: the six acceptance tests from the PRD."""

import json
import os
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A scratch copy of the repo layout so the poller writes nowhere real."""
    (tmp_path / "fixtures").mkdir()
    for f in (ROOT / "fixtures").glob("*.json"):
        shutil.copy(f, tmp_path / "fixtures" / f.name)
    monkeypatch.setenv("R2WR_ROOT", str(tmp_path))
    monkeypatch.setenv("SESSION_KEY", "a test key that is long enough")
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    monkeypatch.delenv("APPRISE_URL", raising=False)
    import importlib

    from app import state as st

    importlib.reload(st)
    import app.poller as poller

    importlib.reload(poller)
    return tmp_path, poller


def _read(root: Path, name: str):
    return json.loads((root / "docs" / "data" / name).read_text())


def test_1_mock_boot_shows_rung_3(repo):
    root, poller = repo
    assert poller.run(mock=True, fixture="order.pending_fulfillment", notify=False) == 0
    meta, rungs, latest, changes = _read(root, "meta.json"), _read(root, "rungs.json"), _read(root, "latest.json"), _read(root, "changes.json")
    assert meta["rung"] == 4 and meta["mode"] == "mock" and meta["ok"]
    assert latest["fulfillmentSummaryStatus"] == "PENDING_FULFILLMENT"
    assert changes == []  # first poll writes no change rows
    assert meta["fields_watched"] > 100


def test_2_build_matched_advances_and_alerts(repo, monkeypatch):
    root, poller = repo
    sent = []
    monkeypatch.setattr(poller, "send", lambda headline, body, priority="default", tags="": sent.append((headline, body, priority)) or True)
    assert poller.run(mock=True, fixture="order.pending_fulfillment", notify=True) == 0
    assert len(sent) == 1 and sent[0][0] == "Checked, no change" and sent[0][2] == "low"
    sent.clear()
    assert poller.run(mock=True, fixture="order.build_matched", notify=True) == 0
    assert _read(root, "meta.json")["rung"] == 5
    changes = _read(root, "changes.json")
    paths = {c["path"] for c in changes}
    assert "order.configurationStatus" in paths and "order.vehicleId" in paths
    assert len(sent) == 1
    headline, body, priority = sent[0]
    assert headline == "Build matched" and priority == "high" and "Rung 5 of 9" in body


def test_5_quiet_second_poll_writes_no_changes(repo):
    root, poller = repo
    poller.run(mock=True, fixture="order.pending_fulfillment", notify=False)
    poller.run(mock=True, fixture="order.pending_fulfillment", notify=False)
    meta = _read(root, "meta.json")
    assert meta["changes_this_poll"] == 0 and _read(root, "changes.json") == []


def test_6_noise_path_is_stored_not_alerted(repo, monkeypatch):
    root, poller = repo
    sent = []
    monkeypatch.setattr(poller, "send", lambda *a, **k: sent.append(a) or True)
    poller.run(mock=True, fixture="order.pending_fulfillment", notify=True)
    # Mutate a noise-listed field in a copy of the fixture and poll again.
    fx = json.loads((root / "fixtures" / "order.pending_fulfillment.json").read_text())
    fx["order"]["t2dRefreshEnabled"] = False
    (root / "fixtures" / "noisy.json").write_text(json.dumps(fx))
    poller.run(mock=True, fixture="noisy", notify=True)
    meta = _read(root, "meta.json")
    assert meta["changes_this_poll"] == 1 and meta["noise_this_poll"] == 1 and meta["alertable_this_poll"] == 0
    assert [a[0] for a in sent] == ["Checked, no change", "Checked, no change"]


def test_4_auth_expired_path(repo, monkeypatch):
    root, poller = repo
    from app.rivian import RivianAuthError

    sent = []
    monkeypatch.setattr(poller, "send", lambda headline, body, priority="default", tags="": sent.append((headline, priority)) or True)
    monkeypatch.setattr(poller, "fetch_live", lambda *_: (_ for _ in ()).throw(RivianAuthError("UNAUTHENTICATED")))
    assert poller.run(mock=False, fixture="x", notify=True) == 2
    meta = _read(root, "meta.json")
    assert meta["auth_expired"] is True and meta["ok"] is False
    assert sent and sent[0][0].startswith("Session expired") and sent[0][1] == "high"


def test_3_state_survives_between_runs(repo):
    """Mock stand-in for 'container restart needs no re-login': the previous raw snapshot is reloaded from disk."""
    root, poller = repo
    poller.run(mock=True, fixture="order.pending_fulfillment", notify=False)
    assert (root / "state" / "mock_prev.json").exists()
    poller.run(mock=True, fixture="order.vin_assigned", notify=False)
    assert _read(root, "meta.json")["rung"] == 7
    rungs = {r["rung"]: r for r in _read(root, "rungs.json")}
    assert rungs[1]["source"] == "backfill" and rungs[6]["source"] == "poll"


def test_public_files_never_contain_pii(repo):
    root, poller = repo
    for fx in ("order.pending_fulfillment", "order.build_matched", "order.vin_assigned"):
        poller.run(mock=True, fixture=fx, notify=False)
    for name in ("latest.json", "changes.json", "rungs.json", "meta.json"):
        text = (root / "docs" / "data" / name).read_text()
        for bad in ("REDACTED", "@", "line1", "postalCode", "last4", "amount", "7PDSGABA0SN000000", "N000000"):
            assert bad not in text, f"{name} contains {bad}"


def test_stage_since_and_next_scheduled(repo):
    root, poller = repo
    poller.run(mock=True, fixture="order.pending_fulfillment", notify=False)
    meta = _read(root, "meta.json")
    assert meta["stage_since"] and meta["next_scheduled"] is None
    poller.run(mock=True, fixture="order.build_matched", notify=False)
    meta = _read(root, "meta.json")
    assert meta["next_scheduled"] == {"label": "Planned build", "date": "2026-12-08", "field": "plannedBuildDate"}
