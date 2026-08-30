from __future__ import annotations

import base64
import hashlib
import hmac
import os
from pathlib import Path


class SecretStore:
    """Small dependency-free encrypted-at-rest store for local single-user installs."""

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / ".profile_secret"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _secret(self) -> bytes:
        value = os.environ.get("NICOKARA_PROFILE_SECRET")
        if value:
            return hashlib.sha256(value.encode("utf-8")).digest()
        if not self.path.exists():
            self.path.write_bytes(os.urandom(32))
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        return hashlib.sha256(self.path.read_bytes()).digest()

    def encrypt(self, value: str) -> str:
        key = self._secret()
        nonce = os.urandom(16)
        stream = hashlib.sha512(key + nonce).digest()
        raw = value.encode("utf-8")
        cipher = bytes(byte ^ stream[index % len(stream)] for index, byte in enumerate(raw))
        tag = hmac.new(key, nonce + cipher, hashlib.sha256).digest()[:16]
        return "v1:" + base64.urlsafe_b64encode(nonce + tag + cipher).decode("ascii")

    def decrypt(self, value: str | None) -> str | None:
        if not value:
            return None
        try:
            packed = base64.urlsafe_b64decode(value.removeprefix("v1:"))
            nonce, tag, cipher = packed[:16], packed[16:32], packed[32:]
            key = self._secret()
            if not hmac.compare_digest(tag, hmac.new(key, nonce + cipher, hashlib.sha256).digest()[:16]):
                return None
            stream = hashlib.sha512(key + nonce).digest()
            return bytes(byte ^ stream[index % len(stream)] for index, byte in enumerate(cipher)).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None

