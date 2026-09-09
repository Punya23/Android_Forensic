"""Server-route coverage for POST /api/case/<id>/import/whatsapp.

triage/parsers/whatsapp_batch.py (a real, unit-tested multi-export batch
parser — see tests/test_whatsapp_recovery.py::TestWhatsAppBatchParser) had
zero call sites in pipeline.py or server.py: present on disk, unreachable
from the dashboard. This wires it into the same `/api/case/<id>/import/<app>`
route the Instagram/Snapchat/Telegram non-root import flow already uses, so
an analyst can batch-upload WhatsApp `_chat.txt`/`.zip` exports (and live
`msgstore.db` files) without a device in hand. See docs/NOTES.md "Known gaps".
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def _make_server_app(cases_root: Path):
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


class TestWhatsAppBatchImportRoute(unittest.TestCase):
    def setUp(self):
        self._tmp_cases = tempfile.mkdtemp(prefix="snagr_test_cases_")
        self.addCleanup(shutil.rmtree, self._tmp_cases, ignore_errors=True)

        try:
            self.app, _ = _make_server_app(Path(self._tmp_cases))
        except Exception as exc:
            self.skipTest(f"server.py could not be imported in this environment: {exc}")

        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

        from triage.custody import Case, CaseMeta

        self.case = Case.create(
            Path(self._tmp_cases), CaseMeta(case_id="WA-BATCH-1", examiner="Insp. Rao")
        )

    def _auth_headers(self):
        resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "snagr-demo"},
            content_type="application/json",
        )
        data = json.loads(resp.data or b"{}")
        return {
            "X-CSRF-Token": data.get("csrf_token", ""),
            "Authorization": f"Bearer {data.get('token', '')}",
        }

    def _post_files(self, files, extra_headers=None):
        headers = self._auth_headers()
        if extra_headers:
            headers.update(extra_headers)
        return self.client.post(
            f"/api/case/{self.case.meta.case_id}/import/whatsapp",
            data=files,
            headers=headers,
            content_type="multipart/form-data",
        )

    def test_unknown_app_name_still_404s(self):
        headers = self._auth_headers()
        resp = self.client.post(
            f"/api/case/{self.case.meta.case_id}/import/not_a_real_app",
            data={"file": (io.BytesIO(b"x"), "x.txt")},
            headers=headers,
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 404)

    def test_no_file_uploaded_is_rejected(self):
        resp = self._post_files({})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", json.loads(resp.data))

    def test_batch_import_merges_into_messages_dataset(self):
        chat_txt = (
            "[06/07/2026, 09:00:00] Rahul: Good morning!\n"
            "[06/07/2026, 09:01:30] Priya: Morning!\n"
        ).encode("utf-8")

        resp = self._post_files(
            {"file": (io.BytesIO(chat_txt), "WhatsApp Chat with Priya/_chat.txt")}
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        body = json.loads(resp.data)
        self.assertEqual(body["imported"], 2)
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["stats"]["total"], 2)

        # Actually landed in the shared "messages" dataset, same as a live acquisition —
        # not a side dataset the rest of the dashboard never reads.
        merged = self.case.read_derived("messages")
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["app"], "whatsapp")

    def test_batch_import_appends_to_existing_messages(self):
        self.case.write_derived(
            "messages", [{"app": "sms", "body": "pre-existing", "timestamp": ""}]
        )

        chat_txt = b"[06/07/2026, 09:00:00] Rahul: Good morning!\n"
        resp = self._post_files(
            {"file": (io.BytesIO(chat_txt), "_chat.txt")}
        )
        self.assertEqual(resp.status_code, 200, resp.data)

        merged = self.case.read_derived("messages")
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["app"], "sms")
        self.assertEqual(merged[1]["app"], "whatsapp")

    def test_unparseable_files_return_400_with_stats(self):
        resp = self._post_files(
            {"file": (io.BytesIO(b"not a whatsapp export"), "random.bin")}
        )
        self.assertEqual(resp.status_code, 400)
        body = json.loads(resp.data)
        self.assertIn("error", body)
        self.assertEqual(body["stats"]["total"], 0)


if __name__ == "__main__":
    unittest.main()
