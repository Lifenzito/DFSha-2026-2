from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from .config import DataNodeSettings
from .security import decrypt_block, encrypt_block, verify_token


class RepairRequest(BaseModel):
    generation: int
    targets: list[str]


def _safe_id(block_id: str) -> str:
    if not block_id or any(character not in "0123456789abcdef-" for character in block_id.lower()):
        raise HTTPException(status_code=400, detail="invalid block id")
    return block_id.lower()


class BlockStore:
    def __init__(self, root: Path, key: bytes):
        self.root = root
        self.key = key
        self.root.mkdir(parents=True, exist_ok=True)

    def _directory(self, block_id: str) -> Path:
        target = self.root / block_id[:2]
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _paths(self, block_id: str, generation: int) -> tuple[Path, Path]:
        directory = self._directory(block_id)
        stem = f"{block_id}.{generation}"
        return directory / f"{stem}.blk", directory / f"{stem}.meta"

    def write(self, block_id: str, generation: int, plaintext: bytes) -> dict[str, Any]:
        block_id = _safe_id(block_id)
        sha256 = hashlib.sha256(plaintext).hexdigest()
        block_path, meta_path = self._paths(block_id, generation)
        encrypted = encrypt_block(plaintext, self.key, aad=f"{block_id}:{generation}".encode())
        temp_block = block_path.with_suffix(".blk.tmp")
        temp_meta = meta_path.with_suffix(".meta.tmp")
        metadata = {"block_id": block_id, "generation": generation, "length": len(plaintext), "sha256": sha256}
        with temp_block.open("wb") as stream:
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        with temp_meta.open("w", encoding="utf-8") as stream:
            json.dump(metadata, stream, separators=(",", ":"), sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_block, block_path)
        os.replace(temp_meta, meta_path)
        try:
            directory_fd = os.open(str(block_path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # Windows no permite abrir directorios con os.open; los dos archivos ya
            # fueron sincronizados antes de los renombres atómicos.
            pass
        return metadata

    def _find_meta(self, block_id: str, generation: int | None = None) -> tuple[Path, dict[str, Any]]:
        block_id = _safe_id(block_id)
        directory = self._directory(block_id)
        candidates = (
            [directory / f"{block_id}.{generation}.meta"]
            if generation is not None
            else sorted(directory.glob(f"{block_id}.*.meta"), reverse=True)
        )
        for path in candidates:
            if path.exists():
                return path, json.loads(path.read_text(encoding="utf-8"))
        raise FileNotFoundError(block_id)

    def status(self, block_id: str, generation: int | None = None) -> dict[str, Any]:
        _, metadata = self._find_meta(block_id, generation)
        return metadata

    def read(self, block_id: str, generation: int | None = None) -> tuple[bytes, dict[str, Any]]:
        meta_path, metadata = self._find_meta(block_id, generation)
        block_path = meta_path.with_suffix(".blk")
        raw = block_path.read_bytes()
        plaintext = decrypt_block(
            raw,
            self.key,
            aad=f"{metadata['block_id']}:{metadata['generation']}".encode(),
        )
        if len(plaintext) != int(metadata["length"]) or hashlib.sha256(plaintext).hexdigest() != metadata["sha256"]:
            raise ValueError("block integrity validation failed")
        return plaintext, metadata


def create_app(settings: DataNodeSettings | None = None) -> FastAPI:
    settings = settings or DataNodeSettings.from_env()
    store = BlockStore(settings.common.data_dir / "blocks", bytes.fromhex(settings.encryption_key_hex))
    application = FastAPI(title="DFSha DataNode", version="0.1.0")
    application.state.settings = settings
    application.state.store = store

    @application.get("/health")
    def health() -> dict[str, Any]:
        usage = os.statvfs(settings.common.data_dir) if hasattr(os, "statvfs") else None
        free = usage.f_bavail * usage.f_frsize if usage else None
        return {"status": "ok", "node_id": settings.node_id, "zone": settings.zone, "free_bytes": free}

    def require_internal(value: str | None) -> None:
        if value != settings.common.internal_secret:
            raise HTTPException(status_code=403, detail="internal authentication failed")

    def block_payload(token: str | None, block_id: str, operation: str) -> dict[str, Any]:
        if not token:
            raise HTTPException(status_code=401, detail="missing block token")
        try:
            payload = verify_token(token, settings.common.block_token_secret, operation=operation)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        if payload.get("block_id") != block_id:
            raise HTTPException(status_code=403, detail="token scoped to another block")
        return payload

    @application.put("/v1/blocks/{block_id}")
    async def write_block(
        block_id: str,
        request: Request,
        x_dfsha_token: Annotated[str | None, Header()] = None,
        x_dfsha_chain_index: Annotated[int, Header()] = 0,
    ) -> dict[str, Any]:
        block_id = _safe_id(block_id)
        payload = block_payload(x_dfsha_token, block_id, "write")
        pipeline = [str(value).rstrip("/") for value in payload.get("pipeline", [])]
        if x_dfsha_chain_index >= len(pipeline) or pipeline[x_dfsha_chain_index] != settings.public_url:
            raise HTTPException(status_code=403, detail="DataNode fuera de la cadena autorizada")
        body = await request.body()
        generation = int(payload["generation"])
        metadata = store.write(block_id, generation, body)
        replicas = [settings.public_url]
        errors: list[dict[str, str]] = []
        if x_dfsha_chain_index + 1 < len(pipeline):
            next_node = pipeline[x_dfsha_chain_index + 1]
            try:
                response = httpx.put(
                    f"{next_node}/v1/blocks/{block_id}",
                    content=body,
                    headers={
                        "X-DFSha-Token": x_dfsha_token,
                        "X-DFSha-Chain-Index": str(x_dfsha_chain_index + 1),
                    },
                    timeout=max(5.0, settings.common.request_timeout_s * 4),
                    verify=settings.common.verify,
                )
                response.raise_for_status()
                downstream = response.json()
                replicas.extend(downstream.get("replicas", []))
                errors.extend(downstream.get("errors", []))
            except (httpx.HTTPError, ValueError) as exc:
                errors.append({"node": next_node, "error": type(exc).__name__})
        return {**metadata, "node_id": settings.node_id, "replicas": replicas, "errors": errors}

    @application.get("/v1/blocks/{block_id}")
    def read_block(
        block_id: str,
        x_dfsha_token: Annotated[str | None, Header()] = None,
        offset: int = Query(0, ge=0),
        length: int | None = Query(None, ge=0),
    ) -> Response:
        block_id = _safe_id(block_id)
        block_payload(x_dfsha_token, block_id, "read")
        try:
            plaintext, metadata = store.read(block_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="block not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=500, detail="block corrupt") from exc
        end = len(plaintext) if length is None else min(len(plaintext), offset + length)
        body = plaintext[offset:end]
        return Response(
            content=body,
            media_type="application/octet-stream",
            headers={
                "X-DFSha-SHA256": metadata["sha256"],
                "X-DFSha-Length": str(metadata["length"]),
                "X-DFSha-Generation": str(metadata["generation"]),
            },
        )

    @application.get("/internal/blocks/{block_id}/status")
    def block_status(
        block_id: str,
        x_dfsha_internal: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        require_internal(x_dfsha_internal)
        try:
            return {**store.status(block_id), "node_id": settings.node_id, "endpoint": settings.public_url}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="block not found") from exc

    @application.put("/internal/blocks/{block_id}")
    async def receive_repair(
        block_id: str,
        request: Request,
        generation: int,
        sha256: str,
        x_dfsha_internal: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        require_internal(x_dfsha_internal)
        body = await request.body()
        if hashlib.sha256(body).hexdigest() != sha256:
            raise HTTPException(status_code=400, detail="repair checksum mismatch")
        return {**store.write(block_id, generation, body), "endpoint": settings.public_url}

    @application.post("/internal/blocks/{block_id}/repair")
    def repair_block(
        block_id: str,
        body: RepairRequest,
        x_dfsha_internal: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        require_internal(x_dfsha_internal)
        try:
            plaintext, metadata = store.read(block_id, body.generation)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="block not found") from exc
        copied: list[str] = []
        errors: list[dict[str, str]] = []
        for target in body.targets:
            target = target.rstrip("/")
            try:
                response = httpx.put(
                    f"{target}/internal/blocks/{block_id}",
                    params={"generation": body.generation, "sha256": metadata["sha256"]},
                    content=plaintext,
                    headers={"X-DFSha-Internal": settings.common.internal_secret},
                    timeout=max(5.0, settings.common.request_timeout_s * 4),
                    verify=settings.common.verify,
                )
                response.raise_for_status()
                copied.append(target)
            except httpx.HTTPError as exc:
                errors.append({"node": target, "error": type(exc).__name__})
        return {"source": settings.public_url, "copied": copied, "errors": errors}

    @application.exception_handler(ValueError)
    async def value_error(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": "INVALID_BLOCK", "detail": str(exc)})

    return application


app = create_app()

