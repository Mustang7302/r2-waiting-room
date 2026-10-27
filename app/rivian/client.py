"""Thin, read-only client for Rivian's unofficial GraphQL API.

Auth is three mutations on the gateway: createCsrfToken (csrf-token + a-sess headers),
login (email + password, returns an otpToken because MFA is always on), then
loginWithOTP (returns u-sess). Every later call carries csrf-token, a-sess, u-sess.

Nothing here writes to Rivian. Every query is wrapped so one failure never takes
the whole poll down; the poller records per-endpoint errors in meta.json.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import requests

from . import queries as Q

log = logging.getLogger(__name__)

BASE_HEADERS = {
    "User-Agent": "RivianApp/1304 CFNetwork/1404.0.5 Darwin/22.3.0",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Apollographql-Client-Name": "com.rivian.ios.consumer-apollo-ios",
}

AUTH_MARKERS = ("UNAUTHENTICATED", "UNAUTHORIZED", "SESSION_MANAGER_ERROR", "NOT_AUTHENTICATED")


class RivianError(Exception):
    """Any non-auth failure from Rivian."""


class RivianAuthError(RivianError):
    """The session is gone; the owner must run the login flow again."""


@dataclass
class Session:
    csrf_token: str = ""
    app_session_token: str = ""
    user_session_token: str = ""
    access_token: str = ""
    refresh_token: str = ""
    otp_token: str = ""
    email: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        s = cls()
        for k, v in d.items():
            if hasattr(s, k):
                setattr(s, k, v)
        return s


class RivianClient:
    def __init__(self, session: Session | None = None, timeout: float = 30.0, retries: int = 2):
        self.session = session or Session()
        self.timeout = timeout
        self.retries = retries
        self._http = requests.Session()

    # ----- auth -------------------------------------------------------------

    def create_csrf_token(self) -> None:
        data = self._post(Q.GATEWAY, Q.CREATE_CSRF, self._headers(auth=False))
        tok = data["createCsrfToken"]
        self.session.csrf_token = tok["csrfToken"]
        self.session.app_session_token = tok["appSessionToken"]

    def login_start(self, email: str, password: str) -> bool:
        """Returns True when an OTP was sent (the normal case), False when fully logged in."""
        self.create_csrf_token()
        self.session.email = email
        doc = dict(Q.LOGIN, variables={"email": email, "password": password})
        data = self._post(Q.GATEWAY, doc, self._headers(auth=False))
        login = data["login"]
        if "otpToken" in login:
            self.session.otp_token = login["otpToken"]
            return True
        self._absorb_tokens(login)
        return False

    def login_finish(self, code: str) -> None:
        if not self.session.otp_token:
            raise RivianAuthError("No pending OTP token; run login start first")
        doc = dict(
            Q.LOGIN_WITH_OTP,
            variables={"email": self.session.email, "otpCode": code.strip(), "otpToken": self.session.otp_token},
        )
        data = self._post(Q.GATEWAY, doc, self._headers(auth=False))
        self._absorb_tokens(data["loginWithOTP"])
        self.session.otp_token = ""
        self.session.created_at = time.time()

    def _absorb_tokens(self, login: dict) -> None:
        self.session.access_token = login.get("accessToken", "") or ""
        self.session.refresh_token = login.get("refreshToken", "") or ""
        self.session.user_session_token = login.get("userSessionToken", "") or ""
        if not self.session.user_session_token:
            raise RivianAuthError(f"Login response carried no userSessionToken: {login}")

    # ----- queries ----------------------------------------------------------

    def vehicle_orders(self) -> list[dict]:
        data = self._post(Q.GATEWAY, Q.VEHICLE_ORDERS, self._headers())
        return data["orders"]["data"]

    def order(self, order_id: str) -> tuple[dict, str | None]:
        """Extended selection; fields Rivian's schema rejects are stripped and the query retried,
        so one unknown field costs nothing. Returns (order, note)."""
        data, note = self._post_adaptive(Q.ORDERS, Q.ORDER_EXT, {"id": order_id}, self._headers(t2d=True), Q.ORDER_CORE)
        return data["order"], note

    def transaction_status(self, order_id: str) -> tuple[dict, str | None]:
        """t2d endpoint first; if it is down (503 seen Oct 2026) try the same query on the gateway."""
        notes = []
        for url, label in ((Q.T2D, "t2d"), (Q.GATEWAY, "gateway")):
            try:
                data, note = self._post_adaptive(url, Q.TRANSACTION_STATUS_EXT, {"orderId": order_id}, self._headers(t2d=True), Q.TRANSACTION_STATUS_CORE)
                if note:
                    notes.append(f"{label}: {note}")
                return data["transactionStatus"], "; ".join(notes) or None
            except RivianAuthError:
                raise
            except RivianError as e:
                notes.append(f"{label}: {e}")
        raise RivianError("; ".join(notes))

    def _post_adaptive(self, url: str, doc: dict, variables: dict, headers: dict, fallback: dict | None = None) -> tuple[dict, str | None]:
        """POST a document; on 'Cannot query field X' errors, drop those fields and retry once.
        If that still fails and a fallback document is given, use it. Returns (data, note)."""
        try:
            return self._post(url, dict(doc, variables=variables), headers), None
        except RivianAuthError:
            raise
        except RivianError as e:
            rejected = Q.rejected_fields(str(e))
            if not rejected:
                if fallback is None:
                    raise
                log.warning("query failed (%s); using fallback selection", e)
                return self._post(url, dict(fallback, variables=variables), headers), f"fallback selection used: {e}"
        trimmed = dict(doc, query=Q.strip_fields(doc["query"], rejected))
        try:
            data = self._post(url, dict(trimmed, variables=variables), headers)
            return data, f"schema rejected {sorted(rejected)}; retried without them"
        except RivianAuthError:
            raise
        except RivianError as e2:
            if fallback is None:
                raise
            log.warning("trimmed query failed (%s); using fallback selection", e2)
            return self._post(url, dict(fallback, variables=variables), headers), f"schema rejected {sorted(rejected)}; fallback selection used: {e2}"

    def delivery(self, order_id: str) -> dict | None:
        data = self._post(Q.GATEWAY, dict(Q.DELIVERY, variables={"orderId": order_id}), self._headers())
        return data["delivery"]

    def user_info(self) -> dict:
        data = self._post(Q.GATEWAY, Q.USER_INFO, self._headers())
        return data["currentUser"]

    # ----- plumbing ---------------------------------------------------------

    def _headers(self, auth: bool = True, t2d: bool = False) -> dict:
        h = dict(BASE_HEADERS)
        h["Csrf-Token"] = self.session.csrf_token
        h["A-Sess"] = self.session.app_session_token
        h["Dc-Cid"] = f"m-ios-{uuid.uuid4()}"
        if auth:
            if not self.session.user_session_token:
                raise RivianAuthError("No user session token; run the login flow")
            h["U-Sess"] = self.session.user_session_token
        if t2d:
            h["dc-cid"] = f"t2d--{uuid.uuid4()}--{uuid.uuid4()}"
            h["csrf-token"] = self.session.csrf_token
            h["app-id"] = "t2d"
        return h

    def _post(self, url: str, doc: dict, headers: dict) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                r = self._http.post(url, json=doc, headers=headers, timeout=self.timeout)
            except requests.RequestException as e:
                last = RivianError(f"network error calling {doc.get('operationName')}: {e}")
                time.sleep(2 ** attempt)
                continue
            if r.status_code in (401, 403):
                raise RivianAuthError(f"{doc.get('operationName')}: HTTP {r.status_code}")
            if r.status_code >= 500:
                last = RivianError(f"{doc.get('operationName')}: HTTP {r.status_code}")
                time.sleep(2 ** attempt)
                continue
            try:
                body = r.json()
            except ValueError as e:
                raise RivianError(f"{doc.get('operationName')}: non-JSON response ({r.status_code}): {e}")
            errors = body.get("errors") or []
            if errors:
                text = "; ".join(str(e.get("message") or e) for e in errors)
                codes = " ".join(str((e.get("extensions") or {}).get("code", "")) for e in errors)
                if any(m in (text + codes).upper() for m in AUTH_MARKERS):
                    raise RivianAuthError(f"{doc.get('operationName')}: {text}")
                raise RivianError(f"{doc.get('operationName')}: {text}")
            if r.status_code != 200 or "data" not in body or body["data"] is None:
                raise RivianError(f"{doc.get('operationName')}: HTTP {r.status_code} with no data")
            return body["data"]
        raise last or RivianError("unreachable")
