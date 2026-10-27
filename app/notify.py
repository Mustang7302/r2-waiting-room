"""Push alerts. ntfy by default (no account needed); an Apprise URL overrides."""

from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger(__name__)

TITLE = "R2 Waiting Room"


def _ntfy_url() -> str | None:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    return f"{server}/{topic}" if topic else None


def send(headline: str, body: str, priority: str = "default", tags: str = "", title: str = TITLE) -> bool:
    """Returns True if an alert went out. Never raises; a dead notifier must not fail a poll."""
    apprise = os.environ.get("APPRISE_URL", "").strip()
    url = _ntfy_url()
    text = f"{headline}\n{body}".strip()
    try:
        if apprise:
            r = requests.post(apprise, json={"title": title, "body": text, "type": "warning" if priority == "high" else "info"}, timeout=15)
            r.raise_for_status()
            return True
        if url:
            headers = {"Title": f"{title}: {headline}", "Priority": priority}
            if tags:
                headers["Tags"] = tags
            r = requests.post(url, data=text.encode("utf-8"), headers=headers, timeout=15)
            r.raise_for_status()
            return True
        log.info("no NTFY_TOPIC or APPRISE_URL set; alert not sent: %s", headline)
        return False
    except requests.RequestException as e:
        log.warning("alert failed: %s", e)
        return False


def format_changes(public_changes: list[dict], rung_now: int, newly: list[int], rung_names: dict[int, str]) -> tuple[str, str, str]:
    """(headline, body, priority) for a poll that found something."""
    if newly:
        headline = rung_names[max(newly)]
        priority = "high"
    else:
        n = len(public_changes)
        headline = f"{n} field{'s' if n != 1 else ''} changed"
        priority = "default"
    lines = []
    for c in public_changes[:5]:
        lines.append(f"{c['path']}: {c['old']} → {c['new']}")
    if len(public_changes) > 5:
        lines.append(f"+{len(public_changes) - 5} more")
    lines.append(f"Rung {rung_now} of {len(rung_names)}")
    return headline, "\n".join(lines), priority
