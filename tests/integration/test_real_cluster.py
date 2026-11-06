from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from dfsha.client import DFShaClient, DFShaClientError


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class Service:
    url: str
    process: subprocess.Popen
    data_dir: Path

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


@dataclass
class Cluster:
    mds: list[Service]
    dns: list[Service]
    internal_secret: str

    def stop(self) -> None:
        for service in [*self.mds, *self.dns]:
            service.stop()


def start_service(module: str, port: int, env: dict[str, str], data_dir: Path) -> Service:
    tls = env.get("DFSHA_TLS_ENABLED") == "true"
    url = f"{'https' if tls else 'http'}://127.0.0.1:{port}"
    process_env = os.environ.copy()
    process_env.update(env)
    process_env["DFSHA_DATA_DIR"] = str(data_dir)
    process_env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    args = [sys.executable, "-m", "uvicorn", f"dfsha.{module}:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"]
    if tls:
        args.extend(["--ssl-certfile", env["DFSHA_TLS_CERT"], "--ssl-keyfile", env["DFSHA_TLS_KEY"]])
    process = subprocess.Popen(
        args,
        env=process_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return Service(url=url, process=process, data_dir=data_dir)


def wait_health(services: list[Service], timeout_s: float = 15.0, verify: str | bool = False) -> None:
    deadline = time.monotonic() + timeout_s
    pending = set(service.url for service in services)
    while pending and time.monotonic() < deadline:
        for url in list(pending):
            try:
                if httpx.get(f"{url}/health", timeout=0.5, verify=verify).status_code == 200:
                    pending.remove(url)
            except httpx.HTTPError:
                pass
        time.sleep(0.1)
    assert not pending, f"servicios sin iniciar: {sorted(pending)}"


@pytest.fixture()
def real_cluster(tmp_path: Path, request) -> Cluster:
    tls = bool(getattr(request, "param", False))
    tls_env: dict[str, str] = {}
    cert_root = tmp_path / "certs"
    verify: str | bool = False
    if tls:
        subprocess.run([sys.executable, str(Path(__file__).resolve().parents[2] / "scripts/generate_certs.py"), "--output", str(cert_root)], check=True, stdout=subprocess.DEVNULL)
        verify = str(cert_root / "client/ca.crt")
        tls_env = {"DFSHA_TLS_ENABLED": "true", "DFSHA_TLS_CA": verify, "DFSHA_TLS_CERT": str(cert_root / "mds-1/server.crt"), "DFSHA_TLS_KEY": str(cert_root / "mds-1/server.key")}
    scheme = "https" if tls else "http"
    internal_secret = "integration-internal-secret"
    block_secret = "integration-block-secret"
    jwt_secret = "integration-jwt-secret"
    dn_ports = [free_port() for _ in range(4)]
    dn_urls = [f"{scheme}://127.0.0.1:{port}" for port in dn_ports]
    dns: list[Service] = []
    for index, (port, url) in enumerate(zip(dn_ports, dn_urls), start=1):
        dns.append(
            start_service(
                "datanode_app",
                port,
                {
                    **tls_env,
                    "DFSHA_NODE_ID": f"dn-{index}",
                    "DFSHA_PUBLIC_URL": url,
                    "DFSHA_ZONE": f"zone-{index}",
                    "DFSHA_INTERNAL_SECRET": internal_secret,
                    "DFSHA_BLOCK_TOKEN_SECRET": block_secret,
                    "DFSHA_JWT_SECRET": jwt_secret,
                    "DFSHA_ENCRYPTION_KEY_HEX": f"{index:02x}" * 32,
                },
                tmp_path / f"dn-{index}",
            )
        )
    wait_health(dns, verify=verify)

    mds_ports = [free_port() for _ in range(3)]
    mds_urls = [f"{scheme}://127.0.0.1:{port}" for port in mds_ports]
    mds: list[Service] = []
    for index, (port, url) in enumerate(zip(mds_ports, mds_urls), start=1):
        peers = ",".join(value for value in mds_urls if value != url)
        mds.append(
            start_service(
                "mds_app",
                port,
                {
                    **tls_env,
                    "DFSHA_NODE_ID": f"mds-{index}",
                    "DFSHA_PUBLIC_URL": url,
                    "DFSHA_MDS_PEERS": peers,
                    "DFSHA_DATANODES": ",".join(dn_urls),
                    "DFSHA_INTERNAL_SECRET": internal_secret,
                    "DFSHA_BLOCK_TOKEN_SECRET": block_secret,
                    "DFSHA_JWT_SECRET": jwt_secret,
                    "DFSHA_BOOTSTRAP_USER": "demo",
                    "DFSHA_BOOTSTRAP_PASSWORD": "demo-change-me",
                    "DFSHA_BLOCK_SIZE": "65536",
                    "DFSHA_ELECTION_MIN_S": "0.45",
                    "DFSHA_ELECTION_MAX_S": "0.8",
                    "DFSHA_RAFT_HEARTBEAT_S": "0.12",
                    "DFSHA_REQUEST_TIMEOUT_S": "0.6",
                    "DFSHA_REPAIR_INTERVAL_S": "0.5",
                    "DFSHA_LEASE_TTL_S": "10",
                },
                tmp_path / f"mds-{index}",
            )
        )
    wait_health(mds, verify=verify)
    cluster = Cluster(mds=mds, dns=dns, internal_secret=internal_secret)
    try:
        DFShaClient(mds_urls, timeout_s=1.0, verify=verify).discover_leader(10)
        yield cluster
    finally:
        cluster.stop()


@pytest.mark.integration
def test_rf_security_replication_failure_and_failover(real_cluster: Cluster) -> None:
    client = DFShaClient([service.url for service in real_cluster.mds], timeout_s=1.2)
    client.login("demo", "demo-change-me")

    client.mkdir("/docs")
    content = (b"DFSha-distributed-block\n" * 12000) + os.urandom(4096)
    uploaded = client.put_bytes(content, "/docs/sample.bin")
    assert len(uploaded["blocks"]) >= 4
    assert all(len(block["replicas"]) == 3 for block in uploaded["blocks"])
    assert client.get_bytes("/docs/sample.bin") == content
    assert client.read("/docs/sample.bin", 100, 77) == content[100:177]

    replacement = b"CONCURRENT-SAFE"
    updated_expected = content[:64] + replacement + content[64 + len(replacement) :]
    client.write("/docs/sample.bin", 64, replacement)
    assert client.get_bytes("/docs/sample.bin") == updated_expected
    client.append("/docs/sample.bin", b"-APPEND")
    expected = updated_expected + b"-APPEND"
    assert client.get_bytes("/docs/sample.bin") == expected

    second = DFShaClient([service.url for service in real_cluster.mds], timeout_s=1.2)
    second.login("demo", "demo-change-me")
    lock = client.lock("/docs/sample.bin")
    with pytest.raises(DFShaClientError) as locked:
        second.open_write("/docs/sample.bin")
    assert locked.value.code == "LOCKED"
    client.close_write(lock)

    leader = client.discover_leader()
    create_user = httpx.post(
        f"{leader}/v1/admin/users",
        json={"uid": "intruder", "password": "intruder-password"},
        headers={"X-DFSha-Internal": real_cluster.internal_secret},
        timeout=3,
    )
    assert create_user.status_code == 200
    intruder = DFShaClient([service.url for service in real_cluster.mds], timeout_s=1.2)
    intruder.login("intruder", "intruder-password")
    with pytest.raises(DFShaClientError) as denied:
        intruder.stat("/users/demo/docs/sample.bin")
    assert denied.value.code == "PERMISSION_DENIED"

    encrypted_files = list(real_cluster.dns[0].data_dir.rglob("*.blk"))
    assert encrypted_files
    assert expected[:64] not in encrypted_files[0].read_bytes()

    manifest_before = client.open_read("/docs/sample.bin")["snapshot"]
    failed_url = manifest_before["blocks"][0]["replicas"][0]
    failed_dn = next(service for service in real_cluster.dns if service.url == failed_url)
    failed_dn.stop()
    assert client.get_bytes("/docs/sample.bin") == expected

    deadline = time.monotonic() + 12
    repaired = False
    while time.monotonic() < deadline:
        snapshot = client.open_read("/docs/sample.bin")["snapshot"]
        repaired = all(len(block["replicas"]) == 3 and failed_url not in block["replicas"] for block in snapshot["blocks"])
        if repaired:
            break
        time.sleep(0.5)
    assert repaired, "la reparación S2S no sustituyó la réplica caída"

    old_leader = client.discover_leader()
    next(service for service in real_cluster.mds if service.url == old_leader).stop()
    client.leader_url = None
    new_leader = client.discover_leader(12)
    assert new_leader != old_leader
    assert client.get_bytes("/docs/sample.bin") == expected
    client.mkdir("/after-failover")
    assert client.stat("/after-failover")["kind"] == "dir"


@pytest.mark.integration
def test_performance_two_file_sizes(real_cluster: Cluster) -> None:
    client = DFShaClient([service.url for service in real_cluster.mds], timeout_s=1.5)
    client.login("demo", "demo-change-me")
    client.mkdir("/bench")
    results = []
    for size in (64 * 1024, 512 * 1024):
        put_samples: list[float] = []
        get_samples: list[float] = []
        payload = os.urandom(size)
        for repetition in range(3):
            remote = f"/bench/{size}-{repetition}.bin"
            started = time.perf_counter()
            client.put_bytes(payload, remote)
            put_samples.append(time.perf_counter() - started)
            started = time.perf_counter()
            restored = client.get_bytes(remote)
            get_samples.append(time.perf_counter() - started)
            assert hashlib.sha256(restored).digest() == hashlib.sha256(payload).digest()
        put_median = statistics.median(put_samples)
        get_median = statistics.median(get_samples)
        results.append(
            {
                "size_bytes": size,
                "repetitions": 3,
                "put_median_s": round(put_median, 6),
                "get_median_s": round(get_median, 6),
                "put_mib_s": round(size / put_median / (1024 * 1024), 3),
                "get_mib_s": round(size / get_median / (1024 * 1024), 3),
            }
        )
    print("PERF_RESULT=" + json.dumps(results, sort_keys=True))


@pytest.mark.integration
@pytest.mark.parametrize("real_cluster", [True], indirect=True)
def test_tls_interrupted_upload_snapshot_corruption_and_degraded_write(real_cluster: Cluster, tmp_path: Path) -> None:
    ca = str(tmp_path / "certs/client/ca.crt")
    urls = [service.url for service in real_cluster.mds]
    client = DFShaClient(urls, verify=ca, timeout_s=2)
    client.login("demo", "demo-change-me")
    with pytest.raises(httpx.ConnectError):
        httpx.get(urls[0] + "/health", verify=True, timeout=2)
    with pytest.raises(DFShaClientError):
        DFShaClient(urls, verify=ca).login("demo", "wrong-password")
    client.mkdir("/secure")
    assert client.cd("/secure") == "/secure"
    original = os.urandom(256 * 1024)
    client.put_bytes(original, "/secure/file")
    snapshot = client.open_read("/secure/file")
    session = client.open_write("/secure/file", truncate=True)
    allocation = client._request("POST", "/v1/files/allocate", json={"handle": session["handle"], "epoch": session["lease"]["epoch"], "base_version": session["base_version"], "index":0}).json()
    def interrupted_body():
        yield b"incomplete"
        raise RuntimeError("injected interrupted transfer")
    with pytest.raises(RuntimeError):
        httpx.put(allocation["pipeline"][0] + "/v1/blocks/" + allocation["block_id"], content=interrupted_body(), headers={"X-DFSha-Token":allocation["token"]}, verify=ca, timeout=3)
    assert client.get_bytes("/secure/file") == original
    with pytest.raises(DFShaClientError) as rejected:
        client._request("POST", "/v1/files/commit", json={"handle":session["handle"],"epoch":session["lease"]["epoch"],"base_version":session["base_version"],"size":10,"sha256":hashlib.sha256(b"incomplete").hexdigest(),"blocks":[{"index":0,"block_id":allocation["block_id"],"generation":allocation["generation"],"length":10,"sha256":hashlib.sha256(b"incomplete").hexdigest(),"replicas":allocation["pipeline"]}]})
    assert rejected.value.code == "INSUFFICIENT_REPLICAS"
    client.close(session)
    client.append("/secure/file", b"committed")
    assert b"".join(client._read_block(block) for block in snapshot["snapshot"]["blocks"]) == original
    client.close(snapshot)
    manifest = client.open_read("/secure/file")["snapshot"]
    first = manifest["blocks"][0]
    node = next(service for service in real_cluster.dns if service.url == first["replicas"][0])
    block = next(node.data_dir.rglob(first["block_id"] + ".*.blk"))
    raw = block.read_bytes()
    block.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    assert client.get_bytes("/secure/file") == original + b"committed"
    node.stop()
    client.put_bytes(b"new version with a disconnected replica", "/secure/file")
    assert client.get_bytes("/secure/file") == b"new version with a disconnected replica"
    assert all(node.url not in b["replicas"] for b in client.open_read("/secure/file")["snapshot"]["blocks"])
    client.rm("/secure/file")
    client.cd("/")
    client.rmdir("/secure")
    assert client.ls("/") == []


@pytest.mark.integration
def test_append_holds_lease_before_reading_to_prevent_lost_update(real_cluster: Cluster) -> None:
    urls = [service.url for service in real_cluster.mds]
    first, second = DFShaClient(urls), DFShaClient(urls)
    for client in (first, second):
        client.login("demo", "demo-change-me")
    first.put_bytes(b"base", "/race")
    entered, release = threading.Event(), threading.Event()
    actual_read = first.get_bytes
    def delayed_read(path):
        content = actual_read(path)
        entered.set()
        assert release.wait(5)
        return content
    first.get_bytes = delayed_read
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(first.append, "/race", b"-A")
        try:
            assert entered.wait(5)
            with pytest.raises(DFShaClientError) as locked:
                second.append("/race", b"-B")
            assert locked.value.code == "LOCKED"
        finally:
            release.set()
        pending.result()
    second.append("/race", b"-B")
    assert second.get_bytes("/race") == b"base-A-B"

