from __future__ import annotations

import hashlib
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import MDSSettings
from .metadata_store import MetadataStore, StoreError, normalize_user_path
from .raft import NoQuorumError, NotLeaderError, RaftNode
from .security import hash_password, issue_token, verify_token


class LoginRequest(BaseModel):
    uid: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


class UserCreateRequest(BaseModel):
    uid: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,64}$")
    password: str = Field(min_length=8, max_length=512)


class PathRequest(BaseModel):
    path: str


class OpenRequest(BaseModel):
    path: str
    mode: Literal["read", "write"]
    create: bool = False
    truncate: bool = False


class LeaseRequest(BaseModel):
    handle: str
    epoch: int
    base_version: int


class AllocateRequest(LeaseRequest):
    index: int = Field(ge=0)


class BlockCommit(BaseModel):
    index: int = Field(ge=0)
    block_id: str
    generation: int = Field(ge=1)
    length: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    replicas: list[str]


class CommitRequest(LeaseRequest):
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    blocks: list[BlockCommit]


class CloseRequest(BaseModel):
    handle: str
    epoch: int | None = None
    base_version: int | None = None


class RaftVoteRequest(BaseModel):
    term: int
    candidate_id: str
    last_log_index: int
    last_log_term: int


class RaftAppendRequest(BaseModel):
    term: int
    leader_id: str
    leader_url: str
    prev_log_index: int
    prev_log_term: int
    entries: list[dict[str, Any]]
    leader_commit: int


def create_app(settings: MDSSettings | None = None) -> FastAPI:
    settings = settings or MDSSettings.from_env()
    store = MetadataStore(settings.common.data_dir / "metadata.db")
    store.bootstrap_user(settings.bootstrap_user, settings.bootstrap_password)
    raft = RaftNode(
        node_id=settings.node_id,
        public_url=settings.public_url,
        peers=settings.peers,
        db_path=settings.common.data_dir / "raft.db",
        apply_command=store.apply,
        internal_secret=settings.common.internal_secret,
        verify=settings.common.verify,
        request_timeout_s=settings.common.request_timeout_s,
        election_min_s=settings.election_timeout_min_s,
        election_max_s=settings.election_timeout_max_s,
        heartbeat_s=settings.heartbeat_s,
    )
    repair_stop = threading.Event()
    lease_plan_lock = threading.RLock()

    def _node_status(endpoint: str, block: dict[str, Any]) -> bool:
        try:
            response = httpx.get(
                f"{endpoint.rstrip('/')}/internal/blocks/{block['block_id']}/status",
                headers={"X-DFSha-Internal": settings.common.internal_secret},
                timeout=settings.common.request_timeout_s,
                verify=settings.common.verify,
            )
            if response.status_code != 200:
                return False
            status = response.json()
            return status.get("sha256") == block["sha256"] and int(status.get("length", -1)) == block["length"]
        except (httpx.HTTPError, ValueError, TypeError):
            return False

    def _repair_once() -> None:
        if raft.status()["role"] != "leader":
            return
        for manifest in store.current_manifests():
            for block in manifest["blocks"]:
                healthy = [endpoint for endpoint in block["replicas"] if _node_status(endpoint, block)]
                if len(healthy) >= settings.replication_factor or not healthy:
                    continue
                candidates = [
                    endpoint.rstrip("/")
                    for endpoint in settings.datanodes
                    if endpoint.rstrip("/") not in healthy
                ]
                source = healthy[0]
                for target in candidates:
                    if len(healthy) >= settings.replication_factor:
                        break
                    try:
                        response = httpx.post(
                            f"{source.rstrip('/')}/internal/blocks/{block['block_id']}/repair",
                            json={"generation": block["generation"], "targets": [target]},
                            headers={"X-DFSha-Internal": settings.common.internal_secret},
                            timeout=max(5.0, settings.common.request_timeout_s * 4),
                            verify=settings.common.verify,
                        )
                        if response.status_code == 200 and target in response.json().get("copied", []):
                            healthy.append(target)
                    except (httpx.HTTPError, ValueError):
                        continue
                normalized = list(dict.fromkeys(healthy))
                if normalized != block["replicas"] and len(normalized) >= settings.min_replicas:
                    raft.propose(
                        {
                            "command_id": str(uuid.uuid4()),
                            "type": "update_replicas",
                            "file_id": manifest["file_id"],
                            "version": manifest["version"],
                            "block_id": block["block_id"],
                            "replicas": normalized,
                        }
                    )

    def _repair_loop() -> None:
        while not repair_stop.wait(settings.repair_interval_s):
            try:
                _repair_once()
            except (NotLeaderError, NoQuorumError):
                continue
            except Exception:
                continue

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        raft.start()
        repair_stop.clear()
        repair_thread = threading.Thread(target=_repair_loop, name=f"repair-{settings.node_id}", daemon=True)
        repair_thread.start()
        try:
            yield
        finally:
            repair_stop.set()
            repair_thread.join(timeout=2)
            raft.stop()

    application = FastAPI(title="DFSha Metadata Service", version="0.1.0", lifespan=lifespan)
    application.state.settings = settings
    application.state.store = store
    application.state.raft = raft

    @application.exception_handler(StoreError)
    async def store_error_handler(_: Request, exc: StoreError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.code, "detail": exc.message})

    @application.exception_handler(NotLeaderError)
    async def not_leader_handler(_: Request, exc: NotLeaderError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"error": "NOT_LEADER", "detail": "use el líder", "leader_url": exc.leader_url},
        )

    @application.exception_handler(NoQuorumError)
    async def no_quorum_handler(_: Request, exc: NoQuorumError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"error": "NO_QUORUM", "detail": str(exc)})

    def require_internal(x_dfsha_internal: Annotated[str | None, Header()] = None) -> None:
        if x_dfsha_internal != settings.common.internal_secret:
            raise HTTPException(status_code=403, detail="internal authentication failed")

    def current_uid(authorization: Annotated[str | None, Header()] = None) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        try:
            payload = verify_token(authorization[7:], settings.common.jwt_secret, operation="access")
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        return str(payload["uid"])

    def require_leader() -> None:
        status = raft.status()
        if status["role"] != "leader":
            raise NotLeaderError(status["leader_url"])

    @application.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", **raft.status()}

    @application.post("/internal/raft/vote", dependencies=[Depends(require_internal)])
    def raft_vote(body: RaftVoteRequest) -> dict[str, Any]:
        return raft.handle_request_vote(body.model_dump())

    @application.post("/internal/raft/append", dependencies=[Depends(require_internal)])
    def raft_append(body: RaftAppendRequest) -> dict[str, Any]:
        return raft.handle_append_entries(body.model_dump())

    @application.post("/v1/auth/login")
    def login(body: LoginRequest) -> dict[str, Any]:
        require_leader()
        if not store.authenticate(body.uid, body.password):
            raise HTTPException(status_code=401, detail="credenciales inválidas")
        return {
            "access_token": issue_token({"uid": body.uid, "op": "access"}, settings.common.jwt_secret, 900),
            "token_type": "bearer",
            "expires_in": 900,
        }

    @application.post("/v1/admin/users", dependencies=[Depends(require_internal)])
    def create_user(body: UserCreateRequest) -> dict[str, Any]:
        require_leader()
        if store.user_exists(body.uid):
            raise StoreError("ALREADY_EXISTS", "el usuario ya existe", 409)
        command = {
            "command_id": str(uuid.uuid4()),
            "type": "create_user",
            "uid": body.uid,
            "password_hash": hash_password(body.password),
            "now": time.time(),
        }
        return raft.propose(command)

    @application.get("/v1/fs/list")
    def list_directory(path: str = Query("/"), uid: str = Depends(current_uid)) -> dict[str, Any]:
        require_leader()
        return {"path": path, "entries": store.list_dir(uid, path)}

    @application.get("/v1/fs/stat")
    def stat(path: str, uid: str = Depends(current_uid)) -> dict[str, Any]:
        require_leader()
        return store.stat(uid, path)

    @application.post("/v1/fs/mkdir")
    def mkdir(
        body: PathRequest,
        uid: str = Depends(current_uid),
        x_dfsha_request_id: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        require_leader()
        previous = store.command_result(x_dfsha_request_id)
        if previous:
            return {"path": body.path, "internal_path": previous["path"]}
        internal = store.validate_namespace_mutation(uid, body.path, expect="absent")
        result = raft.propose(
            {"command_id": x_dfsha_request_id or str(uuid.uuid4()), "type": "mkdir", "uid": uid, "path": internal, "now": time.time()}
        )
        return {"path": body.path, "internal_path": result["path"]}

    @application.delete("/v1/fs/rmdir")
    def rmdir(path: str, uid: str = Depends(current_uid)) -> dict[str, Any]:
        require_leader()
        internal = store.validate_namespace_mutation(uid, path, expect="dir")
        raft.propose(
            {"command_id": str(uuid.uuid4()), "type": "rmdir", "uid": uid, "path": internal, "now": time.time()}
        )
        return {"path": path, "removed": True}

    @application.delete("/v1/fs/file")
    def remove_file(path: str, uid: str = Depends(current_uid)) -> dict[str, Any]:
        require_leader()
        internal = store.validate_namespace_mutation(uid, path, expect="file")
        result = raft.propose(
            {"command_id": str(uuid.uuid4()), "type": "remove", "uid": uid, "path": internal, "now": time.time()}
        )
        return {"path": path, "file_id": result["file_id"], "removed": True}

    @application.post("/v1/files/open")
    def open_file(body: OpenRequest, uid: str = Depends(current_uid)) -> dict[str, Any]:
        require_leader()
        if body.mode == "read":
            manifest = _with_read_tokens(store.get_manifest(uid, body.path), uid)
            return {"handle": str(uuid.uuid4()), "mode": "read", "snapshot": manifest}
        # Serialize the state check with its replicated acquisition. Serializing
        # only propose() allows two requests to plan the same free lease.
        with lease_plan_lock:
            plan = store.plan_open_write(
                uid,
                body.path,
                create=body.create,
                truncate=body.truncate,
                lease_ttl_s=settings.lease_ttl_s,
            )
            raft.propose(plan.command)
        response = {**plan.response, "mode": "write", "block_size": settings.block_size}
        if plan.response["existing"] and not body.truncate:
            response["snapshot"] = store.get_manifest(uid, body.path)
        return response

    def _healthy_datanodes() -> list[str]:
        healthy: list[str] = []
        for endpoint in settings.datanodes:
            try:
                response = httpx.get(
                    f"{endpoint}/health",
                    timeout=settings.common.request_timeout_s,
                    verify=settings.common.verify,
                )
                if response.status_code == 200:
                    healthy.append(endpoint.rstrip("/"))
            except httpx.HTTPError:
                continue
        return healthy

    @application.post("/v1/files/allocate")
    def allocate_block(body: AllocateRequest, uid: str = Depends(current_uid)) -> dict[str, Any]:
        require_leader()
        lease = store.validate_commit(uid, body.handle, body.epoch, body.base_version)
        healthy = _healthy_datanodes()
        if len(healthy) < settings.min_replicas:
            raise StoreError("INSUFFICIENT_REPLICAS", "no hay suficientes DataNodes disponibles", 503)
        start = int(hashlib.sha256(f"{lease['file_id']}:{body.index}".encode()).hexdigest(), 16) % len(healthy)
        rotated = healthy[start:] + healthy[:start]
        pipeline = rotated[: min(settings.replication_factor, len(rotated))]
        block_id = str(uuid.uuid4())
        generation = body.epoch * 1_000_000 + body.index + 1
        token = issue_token(
            {
                "op": "write",
                "uid": uid,
                "block_id": block_id,
                "generation": generation,
                "pipeline": pipeline,
            },
            settings.common.block_token_secret,
            300,
        )
        return {
            "block_id": block_id,
            "generation": generation,
            "index": body.index,
            "pipeline": pipeline,
            "token": token,
        }

    def _verify_replicas(block: BlockCommit) -> list[str]:
        verified: list[str] = []
        for endpoint in dict.fromkeys(block.replicas):
            try:
                response = httpx.get(
                    f"{endpoint.rstrip('/')}/internal/blocks/{block.block_id}/status",
                    headers={"X-DFSha-Internal": settings.common.internal_secret},
                    timeout=settings.common.request_timeout_s,
                    verify=settings.common.verify,
                )
                if response.status_code != 200:
                    continue
                status = response.json()
                if status.get("sha256") == block.sha256 and int(status.get("length", -1)) == block.length:
                    verified.append(endpoint.rstrip("/"))
            except (httpx.HTTPError, ValueError, TypeError):
                continue
        return verified

    @application.post("/v1/files/commit")
    def commit_file(body: CommitRequest, uid: str = Depends(current_uid)) -> dict[str, Any]:
        require_leader()
        lease = store.validate_commit(uid, body.handle, body.epoch, body.base_version)
        indices = [block.index for block in body.blocks]
        if indices != list(range(len(indices))):
            raise StoreError("INVALID_MANIFEST", "los índices de bloques deben ser contiguos", 400)
        if sum(block.length for block in body.blocks) != body.size:
            raise StoreError("INVALID_MANIFEST", "el tamaño no coincide con los bloques", 400)
        normalized_blocks: list[dict[str, Any]] = []
        for block in body.blocks:
            replicas = _verify_replicas(block)
            if len(replicas) < settings.min_replicas:
                raise StoreError(
                    "INSUFFICIENT_REPLICAS",
                    f"el bloque {block.index} tiene {len(replicas)} réplicas verificadas",
                    503,
                )
            normalized = block.model_dump()
            normalized["replicas"] = replicas
            normalized_blocks.append(normalized)
        command = {
            "command_id": str(uuid.uuid4()),
            "type": "commit",
            "uid": uid,
            "path": lease["path"],
            "holder": body.handle,
            "epoch": body.epoch,
            "base_version": body.base_version,
            "file_id": lease["file_id"],
            "size": body.size,
            "sha256": body.sha256,
            "blocks": normalized_blocks,
            "now": time.time(),
        }
        return raft.propose(command)

    @application.post("/v1/files/renew")
    def renew_lease(body: LeaseRequest, uid: str = Depends(current_uid)) -> dict[str, Any]:
        require_leader()
        store.validate_commit(uid, body.handle, body.epoch, body.base_version)
        expires_at = time.time() + settings.lease_ttl_s
        return raft.propose(
            {
                "command_id": str(uuid.uuid4()),
                "type": "renew_lease",
                "uid": uid,
                "holder": body.handle,
                "epoch": body.epoch,
                "expires_at": expires_at,
            }
        )

    @application.post("/v1/files/close")
    def close_file(body: CloseRequest, uid: str = Depends(current_uid)) -> dict[str, Any]:
        require_leader()
        if body.epoch is None:
            return {"released": True, "mode": "read", "handle": body.handle}
        return raft.propose(
            {
                "command_id": str(uuid.uuid4()),
                "type": "release_lease",
                "uid": uid,
                "holder": body.handle,
                "epoch": body.epoch,
            }
        )

    @application.post("/v1/files/lock")
    def lock_file(body: PathRequest, uid: str = Depends(current_uid)) -> dict[str, Any]:
        request = OpenRequest(path=body.path, mode="write", create=False, truncate=False)
        return open_file(request, uid)

    @application.get("/v1/files/locations")
    def locations(path: str, uid: str = Depends(current_uid)) -> dict[str, Any]:
        require_leader()
        return _with_read_tokens(store.get_manifest(uid, path), uid)

    def _with_read_tokens(manifest: dict[str, Any], uid: str) -> dict[str, Any]:
        enriched = {**manifest, "blocks": []}
        for block in manifest["blocks"]:
            value = dict(block)
            value["token"] = issue_token(
                {"op": "read", "uid": uid, "block_id": block["block_id"]},
                settings.common.block_token_secret,
                300,
            )
            enriched["blocks"].append(value)
        return enriched

    return application


app = create_app()

