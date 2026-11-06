from __future__ import annotations

import datetime as dt
import ipaddress
import argparse
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


ROOT = Path(__file__).resolve().parents[1] / "certs" / "generated"
NAMES = ["mds-1", "mds-2", "mds-3", "dn-1", "dn-2", "dn-3", "dn-4", "client"]


def write_key(path: Path, key: rsa.RSAPrivateKey) -> None:
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )


def main() -> None:
    global ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT)
    parser.add_argument("--ip", action="append", default=[], help="Additional private IP SAN, repeatable")
    args = parser.parse_args()
    ROOT = args.output.resolve()
    ROOT.mkdir(parents=True, exist_ok=True)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "DFSha Development CA")])
    now = dt.datetime.now(dt.timezone.utc)
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    for name in NAMES:
        directory = ROOT / name
        directory.mkdir(parents=True, exist_ok=True)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cert_subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        san = x509.SubjectAlternativeName(
            [x509.DNSName(name), x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            + [x509.IPAddress(ipaddress.ip_address(value)) for value in args.ip]
        )
        eku = [ExtendedKeyUsageOID.CLIENT_AUTH] if name == "client" else [ExtendedKeyUsageOID.SERVER_AUTH]
        cert = (
            x509.CertificateBuilder()
            .subject_name(cert_subject)
            .issuer_name(ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=825))
            .add_extension(san, critical=False)
            .add_extension(x509.ExtendedKeyUsage(eku), critical=False)
            .sign(ca_key, hashes.SHA256())
        )
        write_key(directory / "server.key", key)
        (directory / "server.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (directory / "ca.crt").write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    print(ROOT)


if __name__ == "__main__":
    main()

