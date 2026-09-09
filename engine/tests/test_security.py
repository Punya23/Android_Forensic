"""Security hardening tests — CSRF, CORS, rate-limiting, input validation.

These tests target the server.py layer without running a real ADB acquisition.
All state-changing POST/PUT/DELETE endpoints must reject requests that lack a
valid X-CSRF-Token header.  GET endpoints must not require CSRF tokens.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Minimal stubs so server.py can be imported without a real Flask-SocketIO
# environment.
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def _make_server_app(cases_root: Path):
    """Import server and return (app, socketio) with acquisition disabled.

    ``server.py`` has no module-level ``app``/``socketio`` — both come out of
    the ``create_app()`` factory (see ``main()``). Every test class here used
    to reach for ``srv.app``/``srv.socketio`` directly, which don't exist:
    that raised ``AttributeError`` inside the ``try`` below and every single
    test in this file silently self-skipped via ``skipTest`` — a whole
    CSRF/CORS/input-validation suite reporting green (well, gray) while
    testing nothing. Fixed by actually calling the factory against a scratch
    case folder, same as the real server's own ``main()`` does.

    Also pins ``SNAGR_AUTH_USER``/``SNAGR_AUTH_PASS`` to match the
    "admin"/"snagr-demo" credentials the tests below log in with — server.py
    reads those env vars once, at module-import time, and without them set
    login always 401s (no demo-mode default matches "snagr-demo" either),
    which starved every auth-then-validate test of a real session/CSRF token.
    """
    # Patch heavy optional deps and auth env vars before import
    with patch.dict("sys.modules", {
        "flask_socketio": MagicMock(),
        "flask_limiter": MagicMock(),
        "flask_limiter.util": MagicMock(),
    }), patch.dict(os.environ, {
        "SNAGR_AUTH_USER": "admin",
        "SNAGR_AUTH_PASS": "snagr-demo",
    }):
        if "triage.server" in sys.modules:
            del sys.modules["triage.server"]
        from triage import server as srv
        return srv.create_app(cases_root=cases_root)


class TestCSRFEnforcement(unittest.TestCase):
    def setUp(self):
        self._tmp_cases = tempfile.mkdtemp(prefix="snagr_test_cases_")
        self.addCleanup(shutil.rmtree, self._tmp_cases, ignore_errors=True)
        try:
            self.app, _ = _make_server_app(Path(self._tmp_cases))
            self.client = self.app.test_client()
            self.app.config["TESTING"] = True
        except Exception as exc:
            self.skipTest(f"server.py could not be imported in this environment: {exc}")

    def _get_csrf_token(self):
        resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "snagr-demo"},
            content_type="application/json",
        )
        data = json.loads(resp.data or b"{}")
        return data.get("csrf_token", "")

    def test_post_acquire_without_csrf_is_rejected(self):
        resp = self.client.post(
            "/api/acquire",
            json={"mode": "mock"},
            content_type="application/json",
        )
        self.assertIn(resp.status_code, (400, 403, 401),
                      "POST /api/acquire must reject missing CSRF token")

    def test_post_acquire_cancel_without_csrf_is_rejected(self):
        resp = self.client.post("/api/acquire/cancel", content_type="application/json")
        self.assertIn(resp.status_code, (400, 403, 401))

    def test_get_device_info_does_not_require_csrf(self):
        resp = self.client.get("/api/device/info")
        # May return 200 or 401 for auth — must NOT be 403 (CSRF)
        self.assertNotEqual(resp.status_code, 403,
                            "GET endpoints must not require CSRF token")


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self._tmp_cases = tempfile.mkdtemp(prefix="snagr_test_cases_")
        self.addCleanup(shutil.rmtree, self._tmp_cases, ignore_errors=True)
        try:
            self.app, _ = _make_server_app(Path(self._tmp_cases))
            self.client = self.app.test_client()
            self.app.config["TESTING"] = True
        except Exception as exc:
            self.skipTest(f"server.py could not be imported in this environment: {exc}")

    def _auth_headers(self):
        resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "snagr-demo"},
            content_type="application/json",
        )
        data = json.loads(resp.data or b"{}")
        csrf = data.get("csrf_token", "")
        token = data.get("token", "")
        return {
            "X-CSRF-Token": csrf,
            "Authorization": f"Bearer {token}",
        }

    def test_path_traversal_in_case_id_rejected(self):
        headers = self._auth_headers()
        resp = self.client.get("/api/case/../../../etc/passwd", headers=headers)
        self.assertIn(resp.status_code, (400, 404),
                      "Path traversal in case_id must be rejected")

    def test_oversized_brief_rejected(self):
        headers = self._auth_headers()
        giant_brief = "A" * 20001
        # The field is "case_description" (triage/intel/planner.py's case brief), not
        # "brief" — the old key here was silently ignored by /api/acquire (never read),
        # so this test always exercised the *default* case_description and could never
        # have failed no matter how server.py validated the real field.
        resp = self.client.post(
            "/api/acquire",
            json={"mode": "mock", "case_description": giant_brief},
            content_type="application/json",
            headers=headers,
        )
        self.assertIn(resp.status_code, (400, 413),
                      "Oversized case_description text must be rejected")


class TestCORSHeaders(unittest.TestCase):
    def setUp(self):
        self._tmp_cases = tempfile.mkdtemp(prefix="snagr_test_cases_")
        self.addCleanup(shutil.rmtree, self._tmp_cases, ignore_errors=True)
        try:
            self.app, _ = _make_server_app(Path(self._tmp_cases))
            self.client = self.app.test_client()
            self.app.config["TESTING"] = True
        except Exception as exc:
            self.skipTest(f"server.py could not be imported in this environment: {exc}")

    def test_non_localhost_origin_not_allowed(self):
        resp = self.client.get(
            "/api/device/info",
            headers={"Origin": "https://evil.example.com"},
        )
        acao = resp.headers.get("Access-Control-Allow-Origin", "")
        self.assertNotIn("evil.example.com", acao,
                         "Non-localhost origin must not appear in ACAO header")


if __name__ == "__main__":
    unittest.main()
