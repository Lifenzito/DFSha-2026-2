from __future__ import annotations

import hashlib
import os
import posixpath
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import httpx


class DFShaClientError(RuntimeError):
    def __init__(self, code: str, detail: str, status_code: int = 0):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.status_code = status_code


class DFShaClient:
    def __init__(
        self,
        mds_urls: list[str] | tuple[str, ...],
        *,
        verify: str | bool = False,
        timeout_s: float = 5.0,
    ):
        if not mds_urls:
            raise ValueError("se requiere al menos un endpoint MDS")
        self.mds_urls = [url.rstrip("/") for url in mds_urls]
        self.leader_url: str | None = None
        self.verify = verify
        self.timeout_s = timeout_s
        self.token: str | None = None
        self.uid: str | None = None
        self.cwd = "/"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def discover_leader(self, deadline_s: float = 8.0) -> str:
        deadline = time.monotonic() + deadline_s
        while time.monotonic() < deadline:
            candidates = ([self.leader_url] if self.leader_url else []) + self.mds_urls
            for endpoint in dict.fromkeys(value for value in candidates if value):
                try:
                    response = httpx.get(
                        f"{endpoint}/health", timeout=self.timeout_s, verify=self.verify
                    )
                    if response.status_code != 200:
                        continue
                    status = response.json()
                    leader = status.get("leader_url")
                    if status.get("role") == "leader":
                        self.leader_url = endpoint
                        return endpoint
                    if leader:
                        self.leader_url = str(leader).rstrip("/")
                except (httpx.HTTPError, ValueError):
                    continue
            time.sleep(0.15)
        raise DFShaClientError("NO_LEADER", "no se encontró un líder MDS disponible")

    def _request(self, method: str, path: str, *, auth: bool = True, **kwargs: Any) -> httpx.Response:
        last_error: Exception | None = None
        base_headers = dict(kwargs.pop("headers", {}))
        if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            base_headers.setdefault("X-DFSha-Request-ID", str(uuid.uuid4()))
        for _ in range(4):
            endpoint = self.leader_url or self.discover_leader()
            headers = dict(base_headers)
            if auth:
                headers.update(self._headers())
            try:
                response = httpx.request(
                    method,
                    f"{endpoint}{path}",
                    headers=headers,
                    timeout=self.timeout_s,
                    verify=self.verify,
                    **kwargs,
                )
            except httpx.HTTPError as exc:
                last_error = exc
                self.leader_url = None
                continue
            if response.status_code == 503:
                try:
                    body = response.json()
                except ValueError:
                    body = {}
                if body.get("error") == "NOT_LEADER":
                    self.leader_url = body.get("leader_url")
                    continue
            if response.is_error:
                try:
                    body = response.json()
                    code = body.get("error", f"HTTP_{response.status_code}")
                    detail = body.get("detail", str(body))
                except ValueError:
                    code = f"HTTP_{response.status_code}"
                    detail = response.text
                raise DFShaClientError(code, detail, response.status_code)
            return response
        raise DFShaClientError("MDS_UNAVAILABLE", str(last_error or "sin respuesta del líder"))

    def login(self, uid: str, password: str) -> None:
        response = self._request(
            "POST",
            "/v1/auth/login",
            auth=False,
            json={"uid": uid, "password": password},
        )
        self.token = response.json()["access_token"]
        self.uid = uid

    def resolve(self, path: str) -> str:
        if path.startswith("/"):
            return posixpath.normpath(path)
        return posixpath.normpath(posixpath.join(self.cwd, path))

    def cd(self, path: str) -> str:
        resolved = self.resolve(path)
        value = self.stat(resolved)
        if value["kind"] != "dir":
            raise DFShaClientError("NOT_A_DIRECTORY", resolved)
        self.cwd = resolved
        return self.cwd

    def ls(self, path: str = ".") -> list[dict[str, Any]]:
        resolved = self.resolve(path)
        return self._request("GET", "/v1/fs/list", params={"path": resolved}).json()["entries"]

    def stat(self, path: str) -> dict[str, Any]:
        return self._request("GET", "/v1/fs/stat", params={"path": self.resolve(path)}).json()

    def mkdir(self, path: str) -> dict[str, Any]:
        return self._request("POST", "/v1/fs/mkdir", json={"path": self.resolve(path)}).json()

    def rmdir(self, path: str) -> dict[str, Any]:
        return self._request("DELETE", "/v1/fs/rmdir", params={"path": self.resolve(path)}).json()

    def rm(self, path: str) -> dict[str, Any]:
        return self._request("DELETE", "/v1/fs/file", params={"path": self.resolve(path)}).json()

    def open_read(self, path: str) -> dict[str, Any]:
        return self._request(
            "POST", "/v1/files/open", json={"path": self.resolve(path), "mode": "read"}
        ).json()

    def open_write(self, path: str, *, create: bool = False, truncate: bool = False) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/files/open",
            json={"path": self.resolve(path), "mode": "write", "create": create, "truncate": truncate},
        ).json()

    def close_write(self, session: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/files/close",
            json={
                "handle": session["handle"],
                "epoch": session["lease"]["epoch"],
                "base_version": session["base_version"],
            },
        ).json()

    def close(self, session: dict[str, Any]) -> dict[str, Any]:
        if session.get("mode") == "read":
            return self._request(
                "POST", "/v1/files/close", json={"handle": session["handle"]}
            ).json()
        return self.close_write(session)

    def lock(self, path: str) -> dict[str, Any]:
        return self._request(
            "POST", "/v1/files/lock", json={"path": self.resolve(path)}
        ).json()

    def put(self, local_path: str | Path, remote_path: str) -> dict[str, Any]:
        return self.put_bytes(Path(local_path).read_bytes(), remote_path)

    def put_bytes(self, content: bytes, remote_path: str, *, create: bool = True) -> dict[str, Any]:
        session = self.open_write(remote_path, create=create, truncate=True)
        try:
            return self._upload_content(content, session)
        except Exception:
            try:
                self.close_write(session)
            except Exception:
                pass
            raise

    def _upload_content(
        self,
        content: bytes,
        session: dict[str, Any],
        *,
        reuse_blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        block_size = int(session["block_size"])
        blocks: list[dict[str, Any]] = []
        for index, start in enumerate(range(0, len(content), block_size)):
            chunk = content[start : start + block_size]
            chunk_sha = hashlib.sha256(chunk).hexdigest()
            old = reuse_blocks[index] if reuse_blocks and index < len(reuse_blocks) else None
            if old and int(old["length"]) == len(chunk) and old["sha256"] == chunk_sha:
                blocks.append(
                    {
                        "index": index,
                        "block_id": old["block_id"],
                        "generation": old["generation"],
                        "length": len(chunk),
                        "sha256": chunk_sha,
                        "replicas": old["replicas"],
                    }
                )
                continue
            allocation = self._request(
                "POST",
                "/v1/files/allocate",
                json={
                    "handle": session["handle"],
                    "epoch": session["lease"]["epoch"],
                    "base_version": session["base_version"],
                    "index": index,
                },
            ).json()
            first = allocation["pipeline"][0]
            response = httpx.put(
                f"{first}/v1/blocks/{allocation['block_id']}",
                content=chunk,
                headers={"X-DFSha-Token": allocation["token"], "X-DFSha-Chain-Index": "0"},
                timeout=max(10.0, self.timeout_s * 4),
                verify=self.verify,
            )
            response.raise_for_status()
            stored = response.json()
            blocks.append(
                {
                    "index": index,
                    "block_id": allocation["block_id"],
                    "generation": allocation["generation"],
                    "length": len(chunk),
                    "sha256": chunk_sha,
                    "replicas": stored["replicas"],
                }
            )
        commit = self._request(
            "POST",
            "/v1/files/commit",
            json={
                "handle": session["handle"],
                "epoch": session["lease"]["epoch"],
                "base_version": session["base_version"],
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "blocks": blocks,
            },
        ).json()
        return {**commit, "size": len(content), "sha256": hashlib.sha256(content).hexdigest(), "blocks": blocks}

    def _read_block(self, block: dict[str, Any]) -> bytes:
        errors: list[str] = []
        for replica in block["replicas"]:
            try:
                response = httpx.get(
                    f"{replica.rstrip('/')}/v1/blocks/{block['block_id']}",
                    headers={"X-DFSha-Token": block["token"]},
                    timeout=max(10.0, self.timeout_s * 4),
                    verify=self.verify,
                )
                response.raise_for_status()
                data = response.content
                if len(data) != int(block["length"]):
                    raise ValueError("longitud incorrecta")
                if hashlib.sha256(data).hexdigest() != block["sha256"]:
                    raise ValueError("SHA-256 incorrecto")
                return data
            except (httpx.HTTPError, ValueError) as exc:
                errors.append(f"{replica}: {exc}")
        raise DFShaClientError("BLOCK_UNAVAILABLE", "; ".join(errors))

    def get_bytes(self, remote_path: str) -> bytes:
        session = self.open_read(remote_path)
        try:
            snapshot = session["snapshot"]
            blocks = snapshot["blocks"]
            by_index: dict[int, bytes] = {}
            if blocks:
                with ThreadPoolExecutor(max_workers=min(8, len(blocks))) as executor:
                    futures = {executor.submit(self._read_block, block): int(block["index"]) for block in blocks}
                    for future in as_completed(futures):
                        by_index[futures[future]] = future.result()
            content = b"".join(by_index[index] for index in sorted(by_index))
            if len(content) != int(snapshot["size"]):
                raise DFShaClientError("FILE_INTEGRITY", "el tamaño reconstruido no coincide")
            if hashlib.sha256(content).hexdigest() != snapshot["sha256"]:
                raise DFShaClientError("FILE_INTEGRITY", "el SHA-256 final no coincide")
            return content
        finally:
            try:
                self.close(session)
            except DFShaClientError:
                pass

    def get(self, remote_path: str, local_path: str | Path) -> dict[str, Any]:
        content = self.get_bytes(remote_path)
        destination = Path(local_path)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_bytes(content)
        os.replace(temporary, destination)
        return {"path": str(destination), "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}

    def read(self, remote_path: str, offset: int, length: int) -> bytes:
        if offset < 0 or length < 0:
            raise ValueError("offset y length deben ser no negativos")
        return self.get_bytes(remote_path)[offset : offset + length]

    def write(self, remote_path: str, offset: int, data: bytes, *, create: bool = False) -> dict[str, Any]:
        try:
            existing = self.get_bytes(remote_path)
        except DFShaClientError as exc:
            if not create or exc.code != "NOT_FOUND":
                raise
            existing = b""
        if offset < 0 or offset > len(existing):
            raise DFShaClientError("INVALID_ARGUMENT", "no se admiten huecos más allá de EOF")
        updated = existing[:offset] + data + existing[offset + len(data) :]
        session = self.open_write(remote_path, create=create, truncate=False)
        try:
            return self._upload_content(updated, session, reuse_blocks=session.get("snapshot", {}).get("blocks"))
        except Exception:
            try:
                self.close_write(session)
            except Exception:
                pass
            raise

    def append(self, remote_path: str, data: bytes) -> dict[str, Any]:
        existing = self.get_bytes(remote_path)
        session = self.open_write(remote_path, create=False, truncate=False)
        try:
            return self._upload_content(
                existing + data,
                session,
                reuse_blocks=session.get("snapshot", {}).get("blocks"),
            )
        except Exception:
            try:
                self.close_write(session)
            except Exception:
                pass
            raise

