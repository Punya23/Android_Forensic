"""Tests for engine/triage/validation_utils.py.

Covers every validate_* function to ensure:
  - Valid values are accepted without error
  - Invalid / dangerous values raise ValueError
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from triage.validation_utils import (
    validate_case_id,
    validate_serial as validate_device_serial,
    validate_mock_path,
    validate_text_field,
    validate_webhook_url,
)


class TestCaseIdValidation(unittest.TestCase):
    def test_valid_alphanumeric(self):
        self.assertEqual(validate_case_id("CASE-2024-001"), "CASE-2024-001")

    def test_valid_hyphens(self):
        self.assertEqual(validate_case_id("case-123"), "case-123")

    def test_underscores_rejected(self):
        # Underscores are outside the allowed set [A-Za-z0-9-]
        with self.assertRaises(ValueError):
            validate_case_id("test_case_123")

    def test_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            validate_case_id("../../etc/passwd")

    def test_slash_in_id_rejected(self):
        with self.assertRaises(ValueError):
            validate_case_id("case/evil")

    def test_null_bytes_rejected(self):
        with self.assertRaises(ValueError):
            validate_case_id("case\x00id")

    def test_empty_string_rejected(self):
        with self.assertRaises(ValueError):
            validate_case_id("")

    def test_too_long_rejected(self):
        # More than 80 chars
        with self.assertRaises(ValueError):
            validate_case_id("a" * 81)


class TestDeviceSerialValidation(unittest.TestCase):
    def test_valid_serial(self):
        self.assertEqual(validate_device_serial("R5CT307ABCD"), "R5CT307ABCD")

    def test_shell_metachar_rejected(self):
        for bad in ["; rm -rf /", "| cat /etc/passwd", "$(whoami)"]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_device_serial(bad)

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            validate_device_serial("")

    def test_too_short_rejected(self):
        # minimum 4 chars
        with self.assertRaises(ValueError):
            validate_device_serial("AB")


class TestMockPathValidation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.corpus_root = Path(self._tmp)
        # Create a real sub-directory as a valid mock
        self.valid_path = self.corpus_root / "valid_corpus"
        self.valid_path.mkdir()

    def test_valid_path_returned(self):
        result = validate_mock_path(str(self.valid_path), self.corpus_root)
        self.assertEqual(result.resolve(), self.valid_path.resolve())

    def test_traversal_rejected(self):
        with self.assertRaises((ValueError, FileNotFoundError)):
            validate_mock_path("../../secrets", self.corpus_root)

    def test_null_byte_rejected(self):
        with self.assertRaises(ValueError):
            validate_mock_path("path\x00evil", self.corpus_root)

    def test_empty_rejected(self):
        with self.assertRaises((ValueError, FileNotFoundError)):
            validate_mock_path("", self.corpus_root)


class TestTextFieldValidation(unittest.TestCase):
    def test_normal_text_accepted(self):
        result = validate_text_field("John's case notes", field_name="notes", max_length=500)
        self.assertIn("John", result)

    def test_exceeds_max_len_rejected(self):
        with self.assertRaises(ValueError):
            validate_text_field("x" * 501, field_name="brief", max_length=500)

    def test_null_bytes_rejected(self):
        with self.assertRaises(ValueError):
            validate_text_field("hello\x00world", field_name="field", max_length=100)

    def test_whitespace_stripped(self):
        result = validate_text_field("  hello  ", field_name="f", max_length=100)
        self.assertEqual(result, "hello")

    def test_default_max_length_accepts_empty(self):
        result = validate_text_field("", field_name="f")
        self.assertEqual(result, "")

    def test_disallow_empty_raises(self):
        with self.assertRaises(ValueError):
            validate_text_field("", field_name="f", allow_empty=False)


class TestWebhookUrlValidation(unittest.TestCase):
    def test_empty_string_returns_empty(self):
        self.assertEqual(validate_webhook_url(""), "")

    def test_none_coerced_to_empty(self):
        # None is coerced to "" since None or "" → ""
        result = validate_webhook_url(None)
        self.assertEqual(result, "")

    def test_localhost_http_accepted(self):
        url = "http://localhost:8080/webhook"
        self.assertEqual(validate_webhook_url(url), url)

    def test_loopback_127_accepted(self):
        url = "http://127.0.0.1:9000/hook"
        self.assertEqual(validate_webhook_url(url), url)

    def test_external_url_rejected(self):
        with self.assertRaises(ValueError):
            validate_webhook_url("https://hooks.example.com/notify")

    def test_file_scheme_rejected(self):
        with self.assertRaises(ValueError):
            validate_webhook_url("file:///etc/passwd")

    def test_javascript_scheme_rejected(self):
        with self.assertRaises(ValueError):
            validate_webhook_url("javascript:alert(1)")


if __name__ == "__main__":
    unittest.main()
