# SNAGR Security Policy

## Overview

SNAGR is a forensic triage tool intended for use by authorised examiners on devices
for which they hold lawful authority. This document describes the security controls
implemented to protect both the integrity of evidence and the examiner's workstation.

---

## Threat Model

| Threat | Mitigation |
|---|---|
| Unauthorised access to the SNAGR API | Authentication token required; login rate-limited |
| Cross-Site Request Forgery (CSRF) | CSRF token enforced on all state-changing endpoints |
| Cross-Origin requests from untrusted pages | CORS locked to localhost origins by default |
| Path traversal via case IDs / file paths | `validation_utils.py` rejects `..`, null bytes, shell metacharacters |
| Oversized inputs causing memory exhaustion | Text fields capped at 20 000 characters; webhook URLs validated |
| Cached stale evidence re-served as live | Cache keyed by SHA-256 + acquisition metadata; `invalidate_for_source` called when source changes |
| Runaway acquisition threads | `CancellationToken` propagated to all worker threads; `POST /api/acquire/cancel` halts them |
| Tier escalation beyond examiner authorisation | Tier boundaries are explicit in `pipeline.py`; no bypass is attempted at any lower tier |

---

## Authentication

- Login endpoint: `POST /api/auth/login`
- Returns a bearer token **and** a CSRF token on success.
- The bearer token must be supplied as `Authorization: Bearer <token>` on every request.
- Failed login attempts are rate-limited (5 attempts / minute per IP by default).

### CSRF Tokens

All `POST`, `PUT`, `PATCH`, and `DELETE` endpoints require the `X-CSRF-Token` header
whose value must match the token issued at login. Omitting or forging this header
returns HTTP 403.

GET endpoints do not require CSRF tokens.

---

## CORS Policy

By default SNAGR accepts requests only from `http://localhost` and `http://127.0.0.1`
(any port). To allow a different origin — e.g. when accessing via a network-bridged VM
— set the environment variable:

```
SNAGR_CORS_ORIGIN=http://192.168.1.100:3000
```

> **Warning**: Broadening CORS to non-loopback addresses exposes the acquisition API
> to any page the examiner's browser has open. Only do this in isolated lab networks.

---

## Input Validation

All externally supplied strings are validated by `engine/triage/validation_utils.py`
before they reach the pipeline or the filesystem:

| Field | Constraint |
|---|---|
| `case_id` | Alphanumeric + `-_` only; max 128 chars; no path separators |
| Device serial | Alphanumeric + `-:` only; no shell metacharacters |
| `mock_path` | Must be under the workspace root; no `..` segments |
| Free-text brief / notes | Max 20 000 characters; null bytes rejected |
| Webhook URL | Must use `http` or `https` scheme; `file:` and `javascript:` rejected |

---

## Content-Addressed Caching

Parser results are cached keyed by `SHA-256(artifact content) + acquisition_config`
so that the same physical file is never re-parsed on subsequent runs of the same case.

- Cache entries are **never** served across different acquisition configurations.
- `invalidate_for_source(path)` must be called whenever the source artifact changes
  (e.g. after a live ADB pull that produced a different file).
- Cache is stored locally under `<case_root>/.snagr_cache/`; it is discarded when
  the case folder is deleted.

---

## Cancellation

The examiner may cancel an in-progress acquisition at any time via
`POST /api/acquire/cancel`. This:

1. Sets a `CancellationToken` flag that all pipeline threads check at each stage.
2. Raises `AcquisitionCancelled` in any thread that next checks the token.
3. Emits a `cancelled` WebSocket event so the UI updates immediately.

Partial results collected before cancellation are preserved in the case folder.

---

## Forensic Integrity Guarantees

SNAGR never:
- Writes to the target device.
- Attempts tier escalation beyond the examiner-selected acquisition tier.
- Performs decryption, ADB backup bypass, or privilege escalation.
- Silently drops evidence — inaccessible artifacts are logged with the reason.

---

## Reporting Vulnerabilities

If you discover a security issue in SNAGR, please report it privately to the
maintainer before public disclosure.
