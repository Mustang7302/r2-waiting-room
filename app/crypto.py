"""AES-256-GCM for the files committed to a public repo (session tokens, raw snapshots).

Key = SHA-256(SESSION_KEY). File format: base64(nonce[12] + ciphertext + tag).
Losing or changing SESSION_KEY makes every .enc file unreadable; the owner then
runs the login flow again and the next poll starts a fresh baseline.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ENV_KEY = "SESSION_KEY"


def _key(secret: str | None = None) -> bytes:
    secret = secret if secret is not None else os.environ.get(ENV_KEY, "")
    if len(secret) < 16:
        raise RuntimeError(f"{ENV_KEY} must be at least 16 characters (found {len(secret)})")
    return hashlib.sha256(secret.encode("utf-8")).digest()


def encrypt_json(obj: Any, secret: str | None = None) -> str:
    nonce = os.urandom(12)
    plain = json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ct = AESGCM(_key(secret)).encrypt(nonce, plain, b"r2wr-v1")
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt_json(blob: str, secret: str | None = None) -> Any:
    raw = base64.b64decode(blob.strip())
    nonce, ct = raw[:12], raw[12:]
    plain = AESGCM(_key(secret)).decrypt(nonce, ct, b"r2wr-v1")
    return json.loads(plain.decode("utf-8"))


def write_enc(path: Path, obj: Any, secret: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encrypt_json(obj, secret) + "\n", encoding="utf-8")


def read_enc(path: Path, secret: str | None = None) -> Any | None:
    if not path.exists():
        return None
    return decrypt_json(path.read_text(encoding="utf-8"), secret)
