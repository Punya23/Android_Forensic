"""Cryptographic hashing helpers.

SHA-256 is the primary algorithm (SWGDE deprecates MD5/SHA-1 as *sole* hashes). We
compute the hash streaming, so multi-GB media files never load fully into memory, and
we record it into the manifest at the moment of extraction.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK = 1024 * 1024  # 1 MiB streaming reads


def hash_file(path: str | Path, algo: str = "sha256") -> str:
    """Return the hex digest of a file, read in streaming chunks."""
    h = hashlib.new(algo)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_bytes(data: bytes, algo: str = "sha256") -> str:
    """Return the hex digest of an in-memory buffer."""
    return hashlib.new(algo, data).hexdigest()


def file_hashes(path: str | Path) -> dict[str, str]:
    """Compute SHA-256 (primary) plus MD5 (legacy/compat) in a single pass.

    MD5 is included only for cross-tool compatibility, never as the sole identity hash.
    """
    sha = hashlib.sha256()
    md5 = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            sha.update(chunk)
            md5.update(chunk)
    return {"sha256": sha.hexdigest(), "md5": md5.hexdigest()}
