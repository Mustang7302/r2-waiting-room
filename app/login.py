"""Two-step sign-in, built for GitHub Actions where there is no interactive prompt.

    python -m app.login start                    # email + password from env -> Rivian sends the code
    python -m app.login finish --code 123456     # code -> state/session.enc

The password is read from RIVIAN_PASSWORD (a repo secret) only in `start` and is
never written anywhere. `start` writes state/pending.enc (csrf, app session, otp
token); `finish` turns it into state/session.enc and deletes pending.enc.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from . import crypto, state
from .rivian import RivianAuthError, RivianClient, RivianError
from .rivian.client import Session

log = logging.getLogger("r2wr.login")


def start(email: str, password: str) -> int:
    if not email or not password:
        print("RIVIAN_EMAIL and RIVIAN_PASSWORD must be set (repo secrets).", file=sys.stderr)
        return 1
    client = RivianClient()
    try:
        otp_sent = client.login_start(email, password)
    except RivianAuthError as e:
        print(f"Rivian rejected the email or password: {e}", file=sys.stderr)
        return 2
    except RivianError as e:
        print(f"Login could not start: {e}", file=sys.stderr)
        return 1
    if otp_sent:
        crypto.write_enc(state.PENDING_ENC, client.session.to_dict())
        print("Code sent. Rivian texted or emailed you a 6-digit code. Run this workflow again with step=finish within 5 minutes.")
        return 0
    crypto.write_enc(state.SESSION_ENC, client.session.to_dict())
    print("Signed in without a code (no MFA on this account). Session saved.")
    return 0


def finish(code: str) -> int:
    pending = crypto.read_enc(state.PENDING_ENC)
    if not pending:
        print("No pending login. Run step=start first.", file=sys.stderr)
        return 1
    if not code or not code.strip().isdigit():
        print("A numeric code is required for step=finish.", file=sys.stderr)
        return 1
    client = RivianClient(Session.from_dict(pending))
    try:
        client.login_finish(code)
    except RivianAuthError as e:
        print(f"Rivian rejected the code (expired or mistyped). Run start again, then finish within 5 minutes. {e}", file=sys.stderr)
        return 2
    except RivianError as e:
        print(f"Login could not finish: {e}", file=sys.stderr)
        return 1
    crypto.write_enc(state.SESSION_ENC, client.session.to_dict())
    try:
        state.PENDING_ENC.unlink()
    except FileNotFoundError:
        pass
    print("Signed in. Session saved to state/session.enc.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Two-step Rivian login for R2 Waiting Room.")
    p.add_argument("step", choices=["start", "finish"])
    p.add_argument("--code", default="")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if a.step == "start":
        return start(os.environ.get("RIVIAN_EMAIL", "").strip(), os.environ.get("RIVIAN_PASSWORD", ""))
    return finish(a.code)


if __name__ == "__main__":
    sys.exit(main())
