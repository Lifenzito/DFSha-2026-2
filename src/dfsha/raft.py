from __future__ import annotations

import json
import logging
import random
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

import httpx


LOGGER = logging.getLogger("dfsha.raft")


class NotLeaderError(RuntimeError):
    def __init__(self, leader_url: str | None):
        super().__init__("el nodo no es líder")
        self.leader_url = leader_url


class NoQuorumError(RuntimeError):
    pass


class RaftNode:
    """Implementación Raft mínima para el prototipo.

    Incluye elección, log replicado, mayoría y fencing por término. No implementa
    snapshots ni cambio dinámico de membresía; esas limitaciones se documentan.
    """

    def __init__(
        self,
        *,
        node_id: str,
        public_url: str,
        peers: tuple[str, ...],
        db_path: Path,
        apply_command: Callable[[dict[str, Any]], dict[str, Any]],
        internal_secret: str,
        verify: str | bool,
        request_timeout_s: float,
        election_min_s: float,
        election_max_s: float,
        heartbeat_s: float,
    ):
        self.node_id = node_id
        self.public_url = public_url
        self.peers = tuple(peer.rstrip("/") for peer in peers if peer.rstrip("/") != public_url)
        self.db_path = db_path
        self.apply_command = apply_command
        self.internal_secret = internal_secret
        self.verify = verify
        self.request_timeout_s = request_timeout_s
        self.election_min_s = election_min_s
        self.election_max_s = election_max_s
        self.heartbeat_s = heartbeat_s
        self._lock = threading.RLock()
        self._propose_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.role = "follower"
        self.leader_url: str | None = None
        self._next_index: dict[str, int] = {}
        self._match_index: dict[str, int] = {}
        self._last_heartbeat_sent = 0.0
        self._deadline = 0.0
        self._initialize()
        self._reset_deadline()
        if not self.peers:
            self.role = "leader"
            self.leader_url = self.public_url

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS raft_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    current_term INTEGER NOT NULL,
                    voted_for TEXT,
                    commit_index INTEGER NOT NULL,
                    last_applied INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO raft_state(singleton,current_term,voted_for,commit_index,last_applied)
                    VALUES(1,0,NULL,0,0);
                CREATE TABLE IF NOT EXISTS raft_log (
                    idx INTEGER PRIMARY KEY,
                    term INTEGER NOT NULL,
                    command_json TEXT NOT NULL
                );
                """
            )

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"raft-{self.node_id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _state(self) -> sqlite3.Row:
        with self._connect() as connection:
            return connection.execute("SELECT * FROM raft_state WHERE singleton=1").fetchone()

    def _update_state(self, **values: Any) -> None:
        if not values:
            return
        assignments = ",".join(f"{key}=?" for key in values)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE raft_state SET {assignments} WHERE singleton=1",
                tuple(values.values()),
            )

    def _last_log(self) -> tuple[int, int]:
        with self._connect() as connection:
            row = connection.execute("SELECT idx,term FROM raft_log ORDER BY idx DESC LIMIT 1").fetchone()
        return (int(row["idx"]), int(row["term"])) if row else (0, 0)

    def _term_at(self, index: int) -> int:
        if index == 0:
            return 0
        with self._connect() as connection:
            row = connection.execute("SELECT term FROM raft_log WHERE idx=?", (index,)).fetchone()
        return int(row["term"]) if row else -1

    def _entries_from(self, index: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT idx,term,command_json FROM raft_log WHERE idx>=? ORDER BY idx", (index,)
            ).fetchall()
        return [
            {"index": int(row["idx"]), "term": int(row["term"]), "command": json.loads(row["command_json"])}
            for row in rows
        ]

    def _reset_deadline(self) -> None:
        self._deadline = time.monotonic() + random.uniform(self.election_min_s, self.election_max_s)

    def status(self) -> dict[str, Any]:
        state = self._state()
        last_index, last_term = self._last_log()
        with self._lock:
            return {
                "node_id": self.node_id,
                "role": self.role,
                "leader_url": self.leader_url,
                "term": int(state["current_term"]),
                "commit_index": int(state["commit_index"]),
                "last_applied": int(state["last_applied"]),
                "last_log_index": last_index,
                "last_log_term": last_term,
                "peers": list(self.peers),
            }

    def _step_down(self, term: int, leader_url: str | None = None) -> None:
        state = self._state()
        updates: dict[str, Any] = {}
        if term > int(state["current_term"]):
            updates = {"current_term": term, "voted_for": None}
        if updates:
            self._update_state(**updates)
        self.role = "follower"
        self.leader_url = leader_url
        self._reset_deadline()

    def handle_request_vote(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            state = self._state()
            current_term = int(state["current_term"])
            term = int(payload["term"])
            if term < current_term:
                return {"term": current_term, "vote_granted": False}
            if term > current_term:
                self._step_down(term)
                state = self._state()
                current_term = term
            last_index, last_term = self._last_log()
            candidate_fresh = (int(payload["last_log_term"]), int(payload["last_log_index"])) >= (
                last_term,
                last_index,
            )
            voted_for = state["voted_for"]
            grant = candidate_fresh and (voted_for is None or voted_for == payload["candidate_id"])
            if grant:
                self._update_state(voted_for=payload["candidate_id"])
                self._reset_deadline()
            return {"term": current_term, "vote_granted": grant}

    def handle_append_entries(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            state = self._state()
            current_term = int(state["current_term"])
            term = int(payload["term"])
            if term < current_term:
                return {"term": current_term, "success": False, "match_index": self._last_log()[0]}
            self._step_down(term, payload.get("leader_url"))
            prev_index = int(payload["prev_log_index"])
            prev_term = int(payload["prev_log_term"])
            if self._term_at(prev_index) != prev_term:
                return {
                    "term": term,
                    "success": False,
                    "match_index": max(0, min(prev_index - 1, self._last_log()[0])),
                }
            entries = payload.get("entries", [])
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                for entry in entries:
                    index = int(entry["index"])
                    existing = connection.execute("SELECT term FROM raft_log WHERE idx=?", (index,)).fetchone()
                    if existing and int(existing["term"]) != int(entry["term"]):
                        connection.execute("DELETE FROM raft_log WHERE idx>=?", (index,))
                        existing = None
                    if not existing:
                        connection.execute(
                            "INSERT INTO raft_log(idx,term,command_json) VALUES(?,?,?)",
                            (
                                index,
                                int(entry["term"]),
                                json.dumps(entry["command"], separators=(",", ":"), sort_keys=True),
                            ),
                        )
                last_index = connection.execute("SELECT COALESCE(MAX(idx),0) AS value FROM raft_log").fetchone()["value"]
                new_commit = min(int(payload.get("leader_commit", 0)), int(last_index))
                if new_commit > int(state["commit_index"]):
                    connection.execute("UPDATE raft_state SET commit_index=? WHERE singleton=1", (new_commit,))
                connection.commit()
            self._apply_committed()
            return {"term": term, "success": True, "match_index": self._last_log()[0]}

    def _headers(self) -> dict[str, str]:
        return {"X-DFSha-Internal": self.internal_secret}

    def _post(self, peer: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = httpx.post(
            f"{peer}{path}",
            json=payload,
            headers=self._headers(),
            timeout=self.request_timeout_s,
            verify=self.verify,
        )
        response.raise_for_status()
        return response.json()

    def _run(self) -> None:
        while not self._stop.wait(0.05):
            try:
                with self._lock:
                    role = self.role
                    deadline = self._deadline
                now = time.monotonic()
                if role == "leader":
                    if now - self._last_heartbeat_sent >= self.heartbeat_s:
                        self._broadcast_append()
                        self._last_heartbeat_sent = now
                elif now >= deadline:
                    self._start_election()
            except Exception:
                LOGGER.exception("error en el bucle Raft de %s", self.node_id)

    def _start_election(self) -> None:
        with self._lock:
            state = self._state()
            term = int(state["current_term"]) + 1
            self._update_state(current_term=term, voted_for=self.node_id)
            self.role = "candidate"
            self.leader_url = None
            self._reset_deadline()
            last_index, last_term = self._last_log()
        payload = {
            "term": term,
            "candidate_id": self.node_id,
            "last_log_index": last_index,
            "last_log_term": last_term,
        }
        votes = 1
        with ThreadPoolExecutor(max_workers=max(1, len(self.peers))) as executor:
            futures = {executor.submit(self._post, peer, "/internal/raft/vote", payload): peer for peer in self.peers}
            for future in as_completed(futures):
                try:
                    response = future.result()
                except Exception:
                    continue
                response_term = int(response.get("term", 0))
                with self._lock:
                    if response_term > int(self._state()["current_term"]):
                        self._step_down(response_term)
                        return
                if response.get("vote_granted"):
                    votes += 1
        if votes >= self.majority:
            with self._lock:
                if self.role != "candidate" or int(self._state()["current_term"]) != term:
                    return
                self.role = "leader"
                self.leader_url = self.public_url
                next_value = self._last_log()[0] + 1
                self._next_index = {peer: next_value for peer in self.peers}
                self._match_index = {peer: 0 for peer in self.peers}
                self._last_heartbeat_sent = 0.0
                LOGGER.info("%s elegido líder para término %s", self.node_id, term)

    @property
    def majority(self) -> int:
        return (len(self.peers) + 1) // 2 + 1

    def _append_payload(self, peer: str) -> dict[str, Any]:
        state = self._state()
        next_index = self._next_index.get(peer, self._last_log()[0] + 1)
        prev_index = next_index - 1
        return {
            "term": int(state["current_term"]),
            "leader_id": self.node_id,
            "leader_url": self.public_url,
            "prev_log_index": prev_index,
            "prev_log_term": self._term_at(prev_index),
            "entries": self._entries_from(next_index),
            "leader_commit": int(state["commit_index"]),
        }

    def _replicate_peer(self, peer: str) -> bool:
        for _ in range(4):
            payload = self._append_payload(peer)
            try:
                response = self._post(peer, "/internal/raft/append", payload)
            except Exception:
                return False
            term = int(response.get("term", 0))
            with self._lock:
                current_term = int(self._state()["current_term"])
                if term > current_term:
                    self._step_down(term)
                    return False
                if response.get("success"):
                    match_index = int(response.get("match_index", payload["prev_log_index"]))
                    self._match_index[peer] = match_index
                    self._next_index[peer] = match_index + 1
                    return True
                self._next_index[peer] = max(1, int(response.get("match_index", 0)) + 1)
        return False

    def _broadcast_append(self) -> int:
        if not self.peers:
            return 1
        acknowledgements = 1
        with ThreadPoolExecutor(max_workers=len(self.peers)) as executor:
            futures = [executor.submit(self._replicate_peer, peer) for peer in self.peers]
            for future in as_completed(futures):
                try:
                    acknowledgements += int(future.result())
                except Exception:
                    continue
        return acknowledgements

    def propose(self, command: dict[str, Any]) -> dict[str, Any]:
        with self._propose_lock:
            with self._lock:
                if self.role != "leader":
                    raise NotLeaderError(self.leader_url)
                state = self._state()
                term = int(state["current_term"])
                index = self._last_log()[0] + 1
                with self._connect() as connection:
                    connection.execute(
                        "INSERT INTO raft_log(idx,term,command_json) VALUES(?,?,?)",
                        (index, term, json.dumps(command, separators=(",", ":"), sort_keys=True)),
                    )
            deadline = time.monotonic() + max(1.0, self.request_timeout_s * 2)
            acknowledgements = 1
            while time.monotonic() < deadline and acknowledgements < self.majority:
                acknowledgements = self._broadcast_append()
                if acknowledgements < self.majority:
                    time.sleep(0.08)
            with self._lock:
                if self.role != "leader":
                    raise NotLeaderError(self.leader_url)
                if acknowledgements < self.majority:
                    raise NoQuorumError("no hubo mayoría para confirmar metadatos")
                self._update_state(commit_index=index)
                result = self._apply_committed().get(index, {})
            self._broadcast_append()
            return result

    def _apply_committed(self) -> dict[int, dict[str, Any]]:
        results: dict[int, dict[str, Any]] = {}
        state = self._state()
        last_applied = int(state["last_applied"])
        commit_index = int(state["commit_index"])
        if last_applied >= commit_index:
            return results
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT idx,command_json FROM raft_log WHERE idx>? AND idx<=? ORDER BY idx",
                (last_applied, commit_index),
            ).fetchall()
        for row in rows:
            index = int(row["idx"])
            command = json.loads(row["command_json"])
            try:
                results[index] = self.apply_command(command)
            except Exception:
                LOGGER.exception("falló la aplicación del comando Raft %s", index)
                raise
            self._update_state(last_applied=index)
        return results

