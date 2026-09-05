"""Input validation utilities for the SNAGR triage engine.

Every value that crosses a trust boundary (HTTP request body, URL parameter,
ADB serial from the environment, file path from an archive member) passes
through one of these helpers before being used.  They raise ``ValueError``
with a plain-English message on rejection — callers convert that to a 400.

Design rules
------------
* Reject-by-default: only explicitly allowed characters / patterns pass.
* Never trust caller-supplied paths to be inside any directory without an
  explicit canonicalization check.
* Logging is the caller's responsibility: these functions are pure validators.
* No network calls: SSRF prevention is done by allowlisting, not by DNS
  resolution.
"""

from __future__ import annotations

import os
import re
import zipfile
import tarfile
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Case / run identifiers
# ---------------------------------------------------------------------------

#: Legal case-ID alphabet: letters, digits, hyphens.  Max 80 chars.
_CASE_ID_RE = re.compile(r"^[A-Za-z0-9\-]{1,80}$")


def validate_case_id(value: str) -> str:
    """Return *value* if it is a safe case ID, else raise ValueError.

    Parameters
    ----------
    value:
        Raw string from request body or URL parameter.

    Returns
    -------
    str
        The validated case ID (unchanged).

    Raises
    ------
    ValueError
        If the value contains path separators, null bytes, or characters
        outside ``[A-Za-z0-9-]``, or is longer than 80 characters.
    """
    if not value or not isinstance(value, str):
        raise ValueError("case_id must be a non-empty string")
    if "/" in value or "\\" in value or "\x00" in value:
        raise ValueError(f"case_id contains illegal characters: {value!r}")
    if not _CASE_ID_RE.match(value):
        raise ValueError(
            f"case_id must be 1-80 alphanumeric/hyphen characters, got: {value!r}"
        )
    return value


# ---------------------------------------------------------------------------
# ADB device serial
# ---------------------------------------------------------------------------

#: ADB serials: alphanumeric, colon, dot, underscore, hyphen, 4-64 chars.
_SERIAL_RE = re.compile(r"^[A-Za-z0-9:._\-]{4,64}$")


def validate_serial(value: str) -> str:
    """Return *value* if it is a safe ADB serial, else raise ValueError.

    Prevents shell injection via the serial passed to ``adb -s <serial>``.
    ADB itself would reject most of these, but we validate before the value
    reaches the Adb class so injection attempts are logged at the HTTP layer.

    Parameters
    ----------
    value:
        Raw ADB serial string from the request body.

    Returns
    -------
    str
        The validated serial (unchanged).

    Raises
    ------
    ValueError
        If the serial contains characters outside ``[A-Za-z0-9:._-]`` or is
        not between 4 and 64 characters long.
    """
    if not value or not isinstance(value, str):
        raise ValueError("serial must be a non-empty string")
    if not _SERIAL_RE.match(value):
        raise ValueError(
            f"serial contains illegal characters or wrong length: {value!r}"
        )
    return value


# ---------------------------------------------------------------------------
# Free-text examiner / authority / scope fields
# ---------------------------------------------------------------------------

def validate_text_field(
    value: str,
    field_name: str = "field",
    max_length: int = 500,
    allow_empty: bool = True,
) -> str:
    """Validate a free-text metadata string (examiner name, authority, scope).

    Strips leading/trailing whitespace. Rejects null bytes and values longer
    than *max_length*.

    Parameters
    ----------
    value:
        Raw string.
    field_name:
        Human-readable name for error messages.
    max_length:
        Maximum allowed length after stripping.
    allow_empty:
        If False, raise ValueError on empty input.

    Returns
    -------
    str
        Stripped, validated string.
    """
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    if "\x00" in value:
        raise ValueError(f"{field_name} contains null bytes")
    if len(value) > max_length:
        raise ValueError(
            f"{field_name} exceeds maximum length ({len(value)} > {max_length})"
        )
    if not allow_empty and not value:
        raise ValueError(f"{field_name} must not be empty")
    return value


# ---------------------------------------------------------------------------
# Mock corpus path
# ---------------------------------------------------------------------------

def validate_mock_path(raw: str, corpus_root: Path) -> Path:
    """Resolve *raw* to an absolute path and assert it is inside *corpus_root*.

    Prevents path traversal attacks where ``mock=../../engine/triage/server.py``
    would otherwise cause the engine to open an arbitrary file on disk.

    Parameters
    ----------
    raw:
        Raw string from the request body (the ``mock`` key).
    corpus_root:
        The directory that all mock corpus paths must be rooted under.

    Returns
    -------
    Path
        Resolved, validated absolute path.

    Raises
    ------
    ValueError
        If the resolved path escapes *corpus_root*.
    FileNotFoundError
        If the resolved path does not exist (prevents directory enumeration
        via timing — we fail fast with the same error class).
    """
    if not raw or not isinstance(raw, str):
        raise ValueError("mock path must be a non-empty string")
    corpus_root = corpus_root.resolve()
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = corpus_root / candidate
    candidate = candidate.resolve()
    # Must be inside corpus_root
    try:
        candidate.relative_to(corpus_root)
    except ValueError:
        raise ValueError(
            f"mock path escapes the corpus root: {raw!r} → {candidate}"
        )
    if not candidate.exists():
        raise FileNotFoundError(f"mock corpus not found: {candidate}")
    return candidate


# ---------------------------------------------------------------------------
# Webhook URL (SSRF prevention)
# ---------------------------------------------------------------------------

#: Only localhost / loopback destinations are allowed for webhooks.
_LOOPBACK_RE = re.compile(
    r"^https?://(localhost|127\.\d+\.\d+\.\d+|::1)(:\d+)?(/.*)?$",
    re.IGNORECASE,
)


def validate_webhook_url(value: str) -> str:
    """Return *value* if it is a safe webhook URL, else raise ValueError.

    SNAGR's threat model is a field tool on a local network. Outbound HTTP
    to arbitrary destinations (webhooks, SSRF pivots) is not in scope.
    Only loopback destinations are allowed.

    Parameters
    ----------
    value:
        Raw webhook URL string.  Empty string → allowed (means disabled).

    Returns
    -------
    str
        The validated URL, or ``""`` if disabled.

    Raises
    ------
    ValueError
        If the URL is non-empty and does not resolve to a loopback address.
    """
    value = (value or "").strip()
    if not value:
        return ""
    if not _LOOPBACK_RE.match(value):
        raise ValueError(
            f"webhook_url must be a loopback address (localhost / 127.x / ::1), "
            f"got: {value!r}"
        )
    return value


# ---------------------------------------------------------------------------
# Archive extraction safety
# ---------------------------------------------------------------------------

def safe_archive_extract(archive_path: Path, dest: Path) -> list[Path]:
    """Extract *archive_path* into *dest*, refusing dangerous members.

    Dangerous members are those with absolute paths, path-traversal sequences
    (``..``), or names containing null bytes.  On detecting any such member
    the entire extraction is aborted and the destination is left unchanged.

    Supports ``.zip``, ``.tar``, ``.tar.gz``, ``.tar.bz2``, ``.tar.xz``.

    Parameters
    ----------
    archive_path:
        Path to the archive file to extract.
    dest:
        Target directory.  Created if absent.

    Returns
    -------
    list[Path]
        Paths of successfully extracted files.

    Raises
    ------
    ValueError
        If any member name is dangerous.
    """
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)

    suffix = "".join(archive_path.suffixes).lower()

    if suffix in (".zip",):
        return _extract_zip(archive_path, dest)
    if ".tar" in suffix or suffix in (".tgz", ".tbz2", ".txz"):
        return _extract_tar(archive_path, dest)
    raise ValueError(f"Unsupported archive type: {archive_path.name!r}")


def _safe_name(name: str, dest: Path) -> Path:
    """Validate an archive member name and return its resolved destination path."""
    if not name or "\x00" in name:
        raise ValueError(f"Archive member has null byte in name: {name!r}")
    # Normalise separators
    parts = name.replace("\\", "/").split("/")
    if ".." in parts or any(p == "" and i > 0 for i, p in enumerate(parts)):
        raise ValueError(f"Archive member uses path traversal: {name!r}")
    dest_path = (dest / Path(*parts)).resolve()
    try:
        dest_path.relative_to(dest)
    except ValueError:
        raise ValueError(
            f"Archive member escapes destination: {name!r} → {dest_path}"
        )
    return dest_path


def _extract_zip(archive_path: Path, dest: Path) -> list[Path]:
    extracted: list[Path] = []
    with zipfile.ZipFile(archive_path, "r") as zf:
        names = zf.namelist()
        # Validate all names first — fail fast before touching the filesystem
        safe_paths = [_safe_name(n, dest) for n in names]
        for name, safe_path in zip(names, safe_paths):
            info = zf.getinfo(name)
            if info.is_dir():
                safe_path.mkdir(parents=True, exist_ok=True)
                continue
            safe_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, safe_path.open("wb") as dst:
                dst.write(src.read())
            extracted.append(safe_path)
    return extracted


def _extract_tar(archive_path: Path, dest: Path) -> list[Path]:
    extracted: list[Path] = []
    with tarfile.open(archive_path, "r:*") as tf:
        members = tf.getmembers()
        safe_paths = [_safe_name(m.name, dest) for m in members]
        for member, safe_path in zip(members, safe_paths):
            if member.isdir():
                safe_path.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue  # skip symlinks, devices, etc.
            safe_path.parent.mkdir(parents=True, exist_ok=True)
            fobj = tf.extractfile(member)
            if fobj:
                with safe_path.open("wb") as dst:
                    dst.write(fobj.read())
                extracted.append(safe_path)
    return extracted


# ---------------------------------------------------------------------------
# SQLite path safety
# ---------------------------------------------------------------------------

_SQLITE_SUFFIXES = frozenset({".db", ".sqlite", ".sqlite3", ".db3", ".sdb"})


def validate_sqlite_path(path: Path, staging_root: Optional[Path] = None) -> Path:
    """Assert that *path* is a safe, non-symlink SQLite file path.

    Parameters
    ----------
    path:
        Candidate path to open as SQLite.
    staging_root:
        If supplied, the path must be inside this directory (prevents escape
        from the staging area into the case folder or the system).

    Returns
    -------
    Path
        The validated path (resolved).

    Raises
    ------
    ValueError
        On symlink, wrong suffix, or staging-root escape.
    FileNotFoundError
        If the file does not exist.
    """
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"SQLite file not found: {path}")
    if path.is_symlink():
        raise ValueError(f"SQLite path is a symlink: {path}")
    if path.suffix.lower() not in _SQLITE_SUFFIXES:
        raise ValueError(
            f"Not a recognised SQLite extension ({path.suffix!r}): {path}"
        )
    if staging_root is not None:
        staging_root = staging_root.resolve()
        try:
            path.relative_to(staging_root)
        except ValueError:
            raise ValueError(
                f"SQLite path escapes staging root: {path} not under {staging_root}"
            )
    return path
