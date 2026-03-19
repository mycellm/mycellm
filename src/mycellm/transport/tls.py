"""TLS certificate generation for QUIC transport.

Generates ephemeral self-signed certificates for QUIC connections.
Identity verification happens at the app layer via NodeHello, not TLS certs.
"""

from __future__ import annotations

import datetime
import tempfile
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def generate_self_signed_cert(
    cert_path: Path | None = None,
    key_path: Path | None = None,
) -> tuple[Path, Path]:
    """Generate an ephemeral self-signed TLS certificate for QUIC.

    These are NOT the identity certificates — they exist purely for the
    TLS 1.3 layer of QUIC. Identity is verified via NodeHello at app layer.
    """
    key = ec.generate_private_key(ec.SECP256R1())

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "mycellm-node"),
    ])

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365)
        )
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    if cert_path is None:
        tmp = tempfile.mkdtemp(prefix="mycellm-tls-")
        cert_path = Path(tmp) / "cert.pem"
        key_path = Path(tmp) / "key.pem"
    elif key_path is None:
        key_path = cert_path.parent / "key.pem"

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )

    return cert_path, key_path
