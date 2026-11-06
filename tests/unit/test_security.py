from __future__ import annotations

import pytest

from dfsha.security import decrypt_block, encrypt_block, hash_password, issue_token, verify_password, verify_token


def test_password_hash_is_salted_and_verifiable() -> None:
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")
    assert first != second
    assert verify_password("correct horse battery staple", first)
    assert not verify_password("wrong", first)


def test_signed_token_rejects_tampering() -> None:
    token = issue_token({"uid": "ana", "op": "read"}, "secret", 60)
    assert verify_token(token, "secret", operation="read")["uid"] == "ana"
    with pytest.raises(ValueError):
        verify_token(token + "x", "secret", operation="read")


def test_aes_gcm_detects_wrong_aad() -> None:
    key = bytes.fromhex("ab" * 32)
    encrypted = encrypt_block(b"distributed bytes", key, aad=b"block-1:7")
    assert b"distributed bytes" not in encrypted
    assert decrypt_block(encrypted, key, aad=b"block-1:7") == b"distributed bytes"
    with pytest.raises(Exception):
        decrypt_block(encrypted, key, aad=b"block-2:7")

