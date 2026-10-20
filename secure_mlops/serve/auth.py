"""API keys for the model endpoint (threat T9): only clients holding a key can ask the model anything.

The server never stores a key, only its SHA-256 hash, in a file of `client-id:hash` lines (mounted from a Secret in
the cluster). A request's key is hashed and compared with every entry in constant time, so neither a timing
difference nor the file itself gives a key away. The client id, which is not secret, is what audit lines and rate
limits are keyed on.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from pathlib import Path

CLIENT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
HASH = re.compile(r"^[0-9a-f]{64}$")


class KeyFileError(ValueError):
    pass


def digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def load(path: str | Path) -> dict[str, str]:
    """{hash: client id}. Blank lines and comments are allowed; anything else malformed is an error."""
    entries: dict[str, str] = {}
    for n, line in enumerate(Path(path).read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        client, _, h = line.partition(":")
        if not CLIENT_ID.match(client) or not HASH.match(h):
            raise KeyFileError(f"{path}:{n}: expected client-id:sha256-hex")
        if h in entries:
            raise KeyFileError(f"{path}:{n}: duplicate key for {client} and {entries[h]}")
        entries[h] = client
    if not entries:
        raise KeyFileError(f"{path}: no keys; an endpoint nobody can call should not start")
    return entries


def authenticate(entries: dict[str, str], presented: str | None) -> str | None:
    """The client id for a presented key, or None. Every entry is compared, in constant time."""
    if not presented:
        return None
    h = digest(presented)
    found = None
    for stored, client in entries.items():
        if hmac.compare_digest(stored, h):
            found = client
    return found


def add_client(path: str | Path, client: str) -> str:
    """Append a new client and return its key, which is shown once and stored nowhere."""
    if not CLIENT_ID.match(client):
        raise KeyFileError(f"client id {client!r} must be lowercase letters, digits and dashes")
    path = Path(path)
    existing = load(path) if path.exists() and path.read_text().strip() else {}
    if client in existing.values():
        raise KeyFileError(f"{client} already has a key; remove its line to rotate it")
    key = "smk_" + secrets.token_urlsafe(32)
    with path.open("a") as f:
        f.write(f"{client}:{digest(key)}\n")
    return key
