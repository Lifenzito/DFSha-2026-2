from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    value = os.getenv(name, default)
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class CommonSettings:
    data_dir: Path
    jwt_secret: str
    block_token_secret: str
    internal_secret: str
    request_timeout_s: float
    tls_enabled: bool
    tls_ca: str | None
    tls_cert: str | None
    tls_key: str | None

    @classmethod
    def from_env(cls) -> "CommonSettings":
        data_dir = Path(os.getenv("DFSHA_DATA_DIR", "./data")).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            data_dir=data_dir,
            jwt_secret=os.getenv("DFSHA_JWT_SECRET", "dev-jwt-secret-change-me"),
            block_token_secret=os.getenv("DFSHA_BLOCK_TOKEN_SECRET", "dev-block-secret-change-me"),
            internal_secret=os.getenv("DFSHA_INTERNAL_SECRET", "dev-internal-secret-change-me"),
            request_timeout_s=float(os.getenv("DFSHA_REQUEST_TIMEOUT_S", "2.5")),
            tls_enabled=_bool("DFSHA_TLS_ENABLED"),
            tls_ca=os.getenv("DFSHA_TLS_CA"),
            tls_cert=os.getenv("DFSHA_TLS_CERT"),
            tls_key=os.getenv("DFSHA_TLS_KEY"),
        )

    @property
    def verify(self) -> str | bool:
        return self.tls_ca if self.tls_enabled and self.tls_ca else False


@dataclass(frozen=True)
class MDSSettings:
    common: CommonSettings
    node_id: str
    public_url: str
    peers: tuple[str, ...]
    election_timeout_min_s: float
    election_timeout_max_s: float
    heartbeat_s: float
    block_size: int
    replication_factor: int
    min_replicas: int
    lease_ttl_s: int
    repair_interval_s: float
    datanodes: tuple[str, ...]
    bootstrap_user: str
    bootstrap_password: str

    @classmethod
    def from_env(cls) -> "MDSSettings":
        common = CommonSettings.from_env()
        return cls(
            common=common,
            node_id=os.getenv("DFSHA_NODE_ID", "mds-1"),
            public_url=os.getenv("DFSHA_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/"),
            peers=_csv("DFSHA_MDS_PEERS"),
            election_timeout_min_s=float(os.getenv("DFSHA_ELECTION_MIN_S", "1.2")),
            election_timeout_max_s=float(os.getenv("DFSHA_ELECTION_MAX_S", "2.2")),
            heartbeat_s=float(os.getenv("DFSHA_RAFT_HEARTBEAT_S", "0.35")),
            block_size=int(os.getenv("DFSHA_BLOCK_SIZE", str(8 * 1024 * 1024))),
            replication_factor=int(os.getenv("DFSHA_REPLICATION_FACTOR", "3")),
            min_replicas=int(os.getenv("DFSHA_MIN_REPLICAS", "2")),
            lease_ttl_s=int(os.getenv("DFSHA_LEASE_TTL_S", "30")),
            repair_interval_s=float(os.getenv("DFSHA_REPAIR_INTERVAL_S", "5")),
            datanodes=_csv(
                "DFSHA_DATANODES",
                "http://127.0.0.1:8101,http://127.0.0.1:8102,http://127.0.0.1:8103",
            ),
            bootstrap_user=os.getenv("DFSHA_BOOTSTRAP_USER", "demo"),
            bootstrap_password=os.getenv("DFSHA_BOOTSTRAP_PASSWORD", "demo-change-me"),
        )


@dataclass(frozen=True)
class DataNodeSettings:
    common: CommonSettings
    node_id: str
    public_url: str
    zone: str
    encryption_key_hex: str

    @classmethod
    def from_env(cls) -> "DataNodeSettings":
        return cls(
            common=CommonSettings.from_env(),
            node_id=os.getenv("DFSHA_NODE_ID", "dn-1"),
            public_url=os.getenv("DFSHA_PUBLIC_URL", "http://127.0.0.1:8101").rstrip("/"),
            zone=os.getenv("DFSHA_ZONE", "zone-a"),
            encryption_key_hex=os.getenv(
                "DFSHA_ENCRYPTION_KEY_HEX",
                "11" * 32,
            ),
        )

