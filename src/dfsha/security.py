from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_token(payload: dict[str, Any], secret: str, ttl_s: int) -> str:
    body = dict(payload)
    body["exp"] = int(time.time()) + ttl_s
    encoded = _b64encode(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
    signature = _b64encode(hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def verify_token(token: str, secret: str, *, operation: str | None = None) -> dict[str, Any]:
    try:
        encoded, supplied = token.split(".", 1)
    except ValueError as exc:
        raise ValueError("malformed token") from exc
    expected = _b64encode(hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, supplied):
        raise ValueError("invalid token signature")
    payload = json.loads(_b64decode(encoded))
    if int(payload.get("exp", 0)) < int(time.time()):
        raise ValueError("expired token")
    if operation and payload.get("op") not in {operation, "*"}:
        raise ValueError("operation not allowed")
    return payload


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${_b64encode(salt)}${_b64encode(derived)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt_b64, expected_b64 = encoded.split("$", 2)
        if algorithm != "scrypt":
            return False
        actual = hash_password(password, salt=_b64decode(salt_b64)).split("$", 2)[2]
        return hmac.compare_digest(actual, expected_b64)
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class EncryptedBlock:
    nonce: bytes
    ciphertext: bytes

    def serialize(self) -> bytes:
        return b"DFSHA1" + self.nonce + self.ciphertext

    @classmethod
    def parse(cls, raw: bytes) -> "EncryptedBlock":
        if len(raw) < 18 or raw[:6] != b"DFSHA1":
            raise ValueError("invalid encrypted block header")
        return cls(nonce=raw[6:18], ciphertext=raw[18:])


def encrypt_block(plaintext: bytes, key: bytes, *, aad: bytes) -> bytes:
    if len(key) != 32:
        raise ValueError("AES-256-GCM requires a 32-byte key")
    nonce = os.urandom(12)
    encrypted = AESGCM(key).encrypt(nonce, plaintext, aad)
    return EncryptedBlock(nonce, encrypted).serialize()


def decrypt_block(raw: bytes, key: bytes, *, aad: bytes) -> bytes:
    encrypted = EncryptedBlock.parse(raw)
    return AESGCM(key).decrypt(encrypted.nonce, encrypted.ciphertext, aad)

