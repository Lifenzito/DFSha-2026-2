from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from dfsha.metadata_store import MetadataStore, StoreError, normalize_user_path


def command(command_id: str, kind: str, **values):
    return {"command_id": command_id, "type": kind, **values}


def test_namespace_lease_and_atomic_commit(tmp_path: Path) -> None:
    store = MetadataStore(tmp_path / "metadata.db")
    store.bootstrap_user("ana", "password-123")
    assert store.authenticate("ana", "password-123")

    store.apply(command("1", "mkdir", uid="ana", path="/users/ana/docs", now=1.0))
    assert store.list_dir("ana", "/")[0]["name"] == "docs"

    plan = store.plan_open_write("ana", "/docs/a.txt", create=True, truncate=True, lease_ttl_s=30)
    store.apply(plan.command)
    with pytest.raises(StoreError) as locked:
        store.plan_open_write("ana", "/docs/a.txt", create=True, truncate=True, lease_ttl_s=30)
    assert locked.value.code == "LOCKED"

    lease = store.validate_commit(
        "ana", plan.response["handle"], plan.response["lease"]["epoch"], plan.response["base_version"]
    )
    payload = b"hello"
    result = store.apply(
        command(
            "2",
            "commit",
            uid="ana",
            path=lease["path"],
            holder=plan.response["handle"],
            epoch=plan.response["lease"]["epoch"],
            base_version=0,
            file_id=lease["file_id"],
            size=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            blocks=[
                {
                    "index": 0,
                    "block_id": "b1",
                    "generation": 1,
                    "length": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "replicas": ["dn1", "dn2"],
                }
            ],
            now=2.0,
        )
    )
    assert result["version"] == 1
    manifest = store.get_manifest("ana", "/docs/a.txt")
    assert manifest["sha256"] == hashlib.sha256(payload).hexdigest()
    assert manifest["blocks"][0]["replicas"] == ["dn1", "dn2"]


def test_user_path_isolation() -> None:
    assert normalize_user_path("ana", "/docs") == "/users/ana/docs"
    with pytest.raises(StoreError) as denied:
        normalize_user_path("ana", "/users/bob/docs")
    assert denied.value.code == "PERMISSION_DENIED"

