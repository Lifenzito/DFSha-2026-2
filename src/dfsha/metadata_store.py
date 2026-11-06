from __future__ import annotations

import json
import posixpath
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .security import hash_password, verify_password


class StoreError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def normalize_user_path(uid: str, path: str, cwd: str = "/") -> str:
    if not path:
        path = cwd
    elif not path.startswith("/"):
        path = posixpath.join(cwd, path)
    logical = posixpath.normpath("/" + path.lstrip("/"))
    if logical == "/.":
        logical = "/"
    if logical.startswith("/users/"):
        expected = f"/users/{uid}"
        if logical != expected and not logical.startswith(expected + "/"):
            raise StoreError("PERMISSION_DENIED", "ruta fuera del espacio del usuario", 403)
        return logical
    return f"/users/{uid}" if logical == "/" else f"/users/{uid}{logical}"


def logical_path(uid: str, internal: str) -> str:
    prefix = f"/users/{uid}"
    if internal == prefix:
        return "/"
    if internal.startswith(prefix + "/"):
        return internal[len(prefix) :]
    raise StoreError("PERMISSION_DENIED", "ruta fuera del espacio del usuario", 403)


def _parent(path: str) -> str:
    return posixpath.dirname(path.rstrip("/")) or "/"


def _name(path: str) -> str:
    return posixpath.basename(path.rstrip("/"))


@dataclass(frozen=True)
class OpenPlan:
    command: dict[str, Any]
    response: dict[str, Any]


class MetadataStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS users (
            uid TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS entries (
            path TEXT PRIMARY KEY,
            parent TEXT NOT NULL,
            name TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('file','dir')),
            owner TEXT NOT NULL,
            file_id TEXT,
            current_version INTEGER NOT NULL DEFAULT 0,
            size INTEGER NOT NULL DEFAULT 0,
            sha256 TEXT,
            mtime REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_entries_parent ON entries(parent);
        CREATE TABLE IF NOT EXISTS versions (
            file_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            manifest_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY(file_id, version)
        );
        CREATE TABLE IF NOT EXISTS leases (
            path TEXT PRIMARY KEY,
            holder TEXT NOT NULL,
            uid TEXT NOT NULL,
            epoch INTEGER NOT NULL,
            expires_at REAL NOT NULL,
            base_version INTEGER NOT NULL,
            file_id TEXT NOT NULL,
            is_new INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS applied_commands (
            command_id TEXT PRIMARY KEY,
            result_json TEXT NOT NULL,
            applied_at REAL NOT NULL
        );
        """
        with self._connect() as connection:
            connection.executescript(schema)

    def bootstrap_user(self, uid: str, password: str) -> None:
        now = time.time()
        root = f"/users/{uid}"
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO users(uid,password_hash,created_at) VALUES(?,?,?)",
                (uid, hash_password(password), now),
            )
            connection.execute(
                "INSERT OR IGNORE INTO entries(path,parent,name,kind,owner,mtime) VALUES(?,?,?,?,?,?)",
                (root, "/users", uid, "dir", uid, now),
            )
            connection.commit()

    def authenticate(self, uid: str, password: str) -> bool:
        with self._connect() as connection:
            row = connection.execute("SELECT password_hash FROM users WHERE uid=?", (uid,)).fetchone()
        return bool(row and verify_password(password, row["password_hash"]))

    def user_exists(self, uid: str) -> bool:
        with self._connect() as connection:
            return connection.execute("SELECT 1 FROM users WHERE uid=?", (uid,)).fetchone() is not None

    def command_result(self, command_id: str | None) -> dict[str, Any] | None:
        if not command_id:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT result_json FROM applied_commands WHERE command_id=?", (command_id,)
            ).fetchone()
        return json.loads(row["result_json"]) if row else None

    def list_dir(self, uid: str, path: str) -> list[dict[str, Any]]:
        internal = normalize_user_path(uid, path)
        with self._connect() as connection:
            entry = connection.execute("SELECT kind,owner FROM entries WHERE path=?", (internal,)).fetchone()
            if not entry:
                raise StoreError("NOT_FOUND", "directorio no encontrado", 404)
            if entry["owner"] != uid:
                raise StoreError("PERMISSION_DENIED", "acceso denegado", 403)
            if entry["kind"] != "dir":
                raise StoreError("NOT_A_DIRECTORY", "la ruta no es un directorio", 400)
            rows = connection.execute(
                "SELECT name,kind,size,current_version,mtime FROM entries WHERE parent=? ORDER BY kind DESC,name",
                (internal,),
            ).fetchall()
        return [dict(row) for row in rows]

    def stat(self, uid: str, path: str) -> dict[str, Any]:
        internal = normalize_user_path(uid, path)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT path,name,kind,size,current_version,sha256,mtime,owner,file_id FROM entries WHERE path=?",
                (internal,),
            ).fetchone()
        if not row or row["owner"] != uid:
            raise StoreError("NOT_FOUND", "ruta no encontrada", 404)
        value = dict(row)
        value["path"] = logical_path(uid, internal)
        return value

    def get_manifest(self, uid: str, path: str, version: int | None = None) -> dict[str, Any]:
        internal = normalize_user_path(uid, path)
        with self._connect() as connection:
            entry = connection.execute("SELECT * FROM entries WHERE path=?", (internal,)).fetchone()
            if not entry or entry["kind"] != "file" or entry["owner"] != uid:
                raise StoreError("NOT_FOUND", "archivo no encontrado", 404)
            selected_version = version or int(entry["current_version"])
            row = connection.execute(
                "SELECT * FROM versions WHERE file_id=? AND version=?",
                (entry["file_id"], selected_version),
            ).fetchone()
        if not row:
            raise StoreError("NOT_FOUND", "versión no encontrada", 404)
        return {
            "path": logical_path(uid, internal),
            "file_id": entry["file_id"],
            "version": selected_version,
            "size": row["size"],
            "sha256": row["sha256"],
            "blocks": json.loads(row["manifest_json"]),
        }

    def current_version(self, uid: str, path: str) -> tuple[int, str | None]:
        internal = normalize_user_path(uid, path)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT current_version,file_id,owner,kind FROM entries WHERE path=?",
                (internal,),
            ).fetchone()
        if not row:
            return 0, None
        if row["owner"] != uid or row["kind"] != "file":
            raise StoreError("PERMISSION_DENIED", "no se puede escribir la ruta", 403)
        return int(row["current_version"]), row["file_id"]

    def current_manifests(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT e.owner,e.path,e.file_id,e.current_version,v.manifest_json
                FROM entries e
                JOIN versions v ON v.file_id=e.file_id AND v.version=e.current_version
                WHERE e.kind='file'
                """
            ).fetchall()
        return [
            {
                "uid": row["owner"],
                "path": row["path"],
                "file_id": row["file_id"],
                "version": int(row["current_version"]),
                "blocks": json.loads(row["manifest_json"]),
            }
            for row in rows
        ]

    def active_lease(self, internal_path: str) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute("SELECT * FROM leases WHERE path=?", (internal_path,)).fetchone()

    def plan_open_write(
        self,
        uid: str,
        path: str,
        *,
        create: bool,
        truncate: bool,
        lease_ttl_s: int,
        command_id: str | None = None,
    ) -> OpenPlan:
        internal = normalize_user_path(uid, path)
        parent = _parent(internal)
        now = time.time()
        with self._connect() as connection:
            parent_row = connection.execute("SELECT kind,owner FROM entries WHERE path=?", (parent,)).fetchone()
            if not parent_row or parent_row["kind"] != "dir" or parent_row["owner"] != uid:
                raise StoreError("NOT_FOUND", "directorio padre no encontrado", 404)
            existing = connection.execute("SELECT * FROM entries WHERE path=?", (internal,)).fetchone()
            if existing and existing["kind"] != "file":
                raise StoreError("IS_A_DIRECTORY", "la ruta es un directorio", 400)
            if not existing and not create:
                raise StoreError("NOT_FOUND", "archivo no encontrado", 404)
            lease = connection.execute("SELECT * FROM leases WHERE path=?", (internal,)).fetchone()
            if lease and float(lease["expires_at"]) > now:
                raise StoreError("LOCKED", "otro escritor posee el lease exclusivo", 423)
            previous_epoch = int(lease["epoch"]) if lease else 0
        base_version = int(existing["current_version"]) if existing else 0
        file_id = existing["file_id"] if existing else str(uuid.uuid4())
        command_id = command_id or str(uuid.uuid4())
        handle = str(uuid.uuid5(uuid.NAMESPACE_URL, f"dfsha:{command_id}"))
        epoch = previous_epoch + 1
        expires_at = now + lease_ttl_s
        command = {
            "command_id": command_id,
            "type": "acquire_lease",
            "uid": uid,
            "path": internal,
            "holder": handle,
            "epoch": epoch,
            "expires_at": expires_at,
            "base_version": base_version,
            "file_id": file_id,
            "is_new": existing is None,
        }
        return OpenPlan(
            command=command,
            response={
                "handle": handle,
                "file_id": file_id,
                "base_version": base_version,
                "write_base_version": base_version,
                "lease": {"epoch": epoch, "expires_at": expires_at},
                "existing": bool(existing),
            },
        )

    def validate_commit(
        self,
        uid: str,
        handle: str,
        epoch: int,
        base_version: int,
    ) -> sqlite3.Row:
        now = time.time()
        with self._connect() as connection:
            lease = connection.execute("SELECT * FROM leases WHERE holder=?", (handle,)).fetchone()
            if not lease or lease["uid"] != uid:
                raise StoreError("STALE_FENCING_TOKEN", "lease inexistente o reemplazado", 409)
            if int(lease["epoch"]) != epoch or float(lease["expires_at"]) <= now:
                raise StoreError("STALE_FENCING_TOKEN", "lease vencido o epoch obsoleto", 409)
            entry = connection.execute("SELECT current_version FROM entries WHERE path=?", (lease["path"],)).fetchone()
            current = int(entry["current_version"]) if entry else 0
            if current != base_version:
                raise StoreError("VERSION_CONFLICT", "la versión base ya no es vigente", 409)
        return lease

    def apply(self, command: dict[str, Any]) -> dict[str, Any]:
        command_id = command["command_id"]
        with self._lock, self._connect() as connection:
            previous = connection.execute(
                "SELECT result_json FROM applied_commands WHERE command_id=?", (command_id,)
            ).fetchone()
            if previous:
                return json.loads(previous["result_json"])
            connection.execute("BEGIN IMMEDIATE")
            result = self._apply_once(connection, command)
            connection.execute(
                "INSERT INTO applied_commands(command_id,result_json,applied_at) VALUES(?,?,?)",
                (command_id, json.dumps(result, separators=(",", ":")), time.time()),
            )
            connection.commit()
            return result

    def _apply_once(self, connection: sqlite3.Connection, command: dict[str, Any]) -> dict[str, Any]:
        kind = command["type"]
        now = float(command.get("now", time.time()))
        if kind == "create_user":
            uid = command["uid"]
            connection.execute(
                "INSERT OR IGNORE INTO users(uid,password_hash,created_at) VALUES(?,?,?)",
                (uid, command["password_hash"], now),
            )
            connection.execute(
                "INSERT OR IGNORE INTO entries(path,parent,name,kind,owner,mtime) VALUES(?,?,?,?,?,?)",
                (f"/users/{uid}", "/users", uid, "dir", uid, now),
            )
            return {"uid": uid}
        if kind == "mkdir":
            path = command["path"]
            connection.execute(
                "INSERT INTO entries(path,parent,name,kind,owner,mtime) VALUES(?,?,?,?,?,?)",
                (path, _parent(path), _name(path), "dir", command["uid"], now),
            )
            return {"path": path}
        if kind == "rmdir":
            path = command["path"]
            if connection.execute("SELECT 1 FROM entries WHERE parent=? LIMIT 1", (path,)).fetchone():
                raise StoreError("NOT_EMPTY", "el directorio no está vacío", 409)
            connection.execute("DELETE FROM entries WHERE path=? AND kind='dir' AND owner=?", (path, command["uid"]))
            return {"path": path}
        if kind == "remove":
            row = connection.execute("SELECT file_id FROM entries WHERE path=? AND kind='file'", (command["path"],)).fetchone()
            if not row:
                raise StoreError("NOT_FOUND", "archivo no encontrado", 404)
            connection.execute("DELETE FROM entries WHERE path=? AND owner=?", (command["path"], command["uid"]))
            connection.execute("DELETE FROM leases WHERE path=?", (command["path"],))
            return {"path": command["path"], "file_id": row["file_id"]}
        if kind == "acquire_lease":
            connection.execute("DELETE FROM leases WHERE path=?", (command["path"],))
            connection.execute(
                "INSERT INTO leases(path,holder,uid,epoch,expires_at,base_version,file_id,is_new) VALUES(?,?,?,?,?,?,?,?)",
                (
                    command["path"],
                    command["holder"],
                    command["uid"],
                    command["epoch"],
                    command["expires_at"],
                    command["base_version"],
                    command["file_id"],
                    int(command["is_new"]),
                ),
            )
            return {
                "handle": command["holder"],
                "epoch": command["epoch"],
                "expires_at": command["expires_at"],
                "file_id": command["file_id"],
                "base_version": command["base_version"],
                "existing": not bool(command["is_new"]),
            }
        if kind == "renew_lease":
            cursor = connection.execute(
                "UPDATE leases SET expires_at=? WHERE holder=? AND uid=? AND epoch=?",
                (command["expires_at"], command["holder"], command["uid"], command["epoch"]),
            )
            if cursor.rowcount != 1:
                raise StoreError("STALE_FENCING_TOKEN", "lease reemplazado", 409)
            return {"expires_at": command["expires_at"]}
        if kind == "release_lease":
            connection.execute(
                "DELETE FROM leases WHERE holder=? AND uid=? AND epoch=?",
                (command["holder"], command["uid"], command["epoch"]),
            )
            return {"released": True}
        if kind == "commit":
            path = command["path"]
            next_version = int(command["base_version"]) + 1
            existing = connection.execute("SELECT file_id FROM entries WHERE path=?", (path,)).fetchone()
            if existing:
                connection.execute(
                    "UPDATE entries SET current_version=?,size=?,sha256=?,mtime=? WHERE path=? AND owner=?",
                    (next_version, command["size"], command["sha256"], now, path, command["uid"]),
                )
            else:
                connection.execute(
                    "INSERT INTO entries(path,parent,name,kind,owner,file_id,current_version,size,sha256,mtime) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        path,
                        _parent(path),
                        _name(path),
                        "file",
                        command["uid"],
                        command["file_id"],
                        next_version,
                        command["size"],
                        command["sha256"],
                        now,
                    ),
                )
            connection.execute(
                "INSERT INTO versions(file_id,version,size,sha256,manifest_json,created_at) VALUES(?,?,?,?,?,?)",
                (
                    command["file_id"],
                    next_version,
                    command["size"],
                    command["sha256"],
                    json.dumps(command["blocks"], separators=(",", ":"), sort_keys=True),
                    now,
                ),
            )
            connection.execute("DELETE FROM leases WHERE holder=?", (command["holder"],))
            return {"path": logical_path(command["uid"], path), "version": next_version}
        if kind == "update_replicas":
            row = connection.execute(
                "SELECT manifest_json FROM versions WHERE file_id=? AND version=?",
                (command["file_id"], command["version"]),
            ).fetchone()
            if not row:
                raise StoreError("NOT_FOUND", "versión no encontrada para reparación", 404)
            blocks = json.loads(row["manifest_json"])
            found = False
            for block in blocks:
                if block["block_id"] == command["block_id"]:
                    block["replicas"] = list(dict.fromkeys(command["replicas"]))
                    found = True
                    break
            if not found:
                raise StoreError("NOT_FOUND", "bloque no encontrado para reparación", 404)
            connection.execute(
                "UPDATE versions SET manifest_json=? WHERE file_id=? AND version=?",
                (
                    json.dumps(blocks, separators=(",", ":"), sort_keys=True),
                    command["file_id"],
                    command["version"],
                ),
            )
            return {"block_id": command["block_id"], "replicas": command["replicas"]}
        raise StoreError("UNKNOWN_COMMAND", f"comando no soportado: {kind}", 500)

    def validate_namespace_mutation(self, uid: str, path: str, *, expect: str | None = None) -> str:
        internal = normalize_user_path(uid, path)
        if internal == f"/users/{uid}":
            raise StoreError("PERMISSION_DENIED", "no se puede modificar la raíz del usuario", 403)
        parent = _parent(internal)
        with self._connect() as connection:
            parent_row = connection.execute("SELECT kind,owner FROM entries WHERE path=?", (parent,)).fetchone()
            row = connection.execute("SELECT kind,owner FROM entries WHERE path=?", (internal,)).fetchone()
        if not parent_row or parent_row["kind"] != "dir" or parent_row["owner"] != uid:
            raise StoreError("NOT_FOUND", "directorio padre no encontrado", 404)
        if expect == "absent" and row:
            raise StoreError("ALREADY_EXISTS", "la ruta ya existe", 409)
        if expect and expect != "absent" and (not row or row["kind"] != expect or row["owner"] != uid):
            raise StoreError("NOT_FOUND", "ruta no encontrada", 404)
        return internal

