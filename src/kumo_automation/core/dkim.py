"""DKIM key management.

DKIM keys MUST be stable: the public key published in DNS has to match the
private key installed on the server. So keys are generated once per
(namespace, domain) and persisted; both DNS generation and deployment read the
same key. `public_txt_from_private` derives the DNS TXT value from a private PEM.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_pem_private_key


def generate_private_pem(bits: int = 2048) -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()


def public_txt_from_private(pem: str) -> str:
    """Return the DKIM DNS TXT value (v=DKIM1; k=rsa; p=...) for a private PEM."""
    key = load_pem_private_key(pem.encode(), password=None)
    der = key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return "v=DKIM1; k=rsa; p=" + base64.b64encode(der).decode()


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", s).strip("_") or "x"


class KeyStore:
    """File-backed DKIM private-key store: <root>/<namespace>/<domain>.key"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, namespace: str, domain: str) -> Path:
        d = self.root / _safe(namespace)
        d.mkdir(parents=True, exist_ok=True)
        return d / (_safe(domain) + ".key")

    def get_or_create(self, namespace: str, domain: str) -> str:
        p = self.path(namespace, domain)
        if p.exists():
            return p.read_text(encoding="utf-8")
        pem = generate_private_pem()
        p.write_text(pem, encoding="utf-8")
        return pem


def keys_for(profile, store: KeyStore, namespace: str) -> dict[str, str]:
    """{domain: private_pem} for every domain on the profile (stable/persisted)."""
    return {dom: store.get_or_create(namespace, dom) for dom in profile.unique_domains() if dom}
