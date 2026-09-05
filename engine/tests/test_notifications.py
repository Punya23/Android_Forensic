"""Tests for triage/notifications/ — real, working plumbing (unlike the fabrication
stubs in triage/security|analytics|integration|advanced_forensics), now wired to fire
on acquisition completion. No test here talks to a real SMTP/Twilio/webhook endpoint —
that's exactly the point: every client must degrade to "logged, not sent" when
unconfigured, never raise, and never block the acquisition it's reporting on.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from triage.notifications.dispatcher import (  # noqa: E402
    NotificationDispatcher,
    NotificationPayload,
    NotificationType,
)
from triage.notifications.email_client import EmailClient  # noqa: E402
from triage.notifications.sms_client import SMSClient  # noqa: E402
from triage.notifications.webhook_client import WebhookClient  # noqa: E402


# --- SMS: unconfigured must degrade, never raise ----------------------------
def test_sms_client_unconfigured_does_not_raise(monkeypatch):
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    client = SMSClient()
    assert client.client is None
    client.send_sms(["+919820044711"], "test message")  # must not raise


def test_sms_client_configured_uses_twilio(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC_fake")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "fake_token")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15551234567")
    with patch("triage.notifications.sms_client.Client") as MockClient:
        client = SMSClient()
        assert client.client is not None
        client.send_sms(["+919820044711"], "hello")
        MockClient.return_value.messages.create.assert_called_once()


def test_sms_client_per_recipient_failure_does_not_abort_the_loop(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC_fake")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "fake_token")
    with patch("triage.notifications.sms_client.Client") as MockClient:
        MockClient.return_value.messages.create.side_effect = Exception("boom")
        client = SMSClient()
        client.send_sms(["+91111", "+91222"], "hello")  # must not raise
        assert MockClient.return_value.messages.create.call_count == 2


# --- Email: STARTTLS/SSL wiring + graceful failure --------------------------
def test_email_client_defaults_to_plain_smtp(monkeypatch):
    monkeypatch.delenv("SMTP_TLS", raising=False)
    client = EmailClient()
    assert client.tls_mode == ""


def test_email_client_send_failure_is_swallowed(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_PORT", "1")  # nothing listens on port 1
    client = EmailClient()
    client.send_email(["x@example.com"], "subject", "<p>hi</p>")  # must not raise


def test_email_client_starttls_mode_calls_starttls(monkeypatch):
    monkeypatch.setenv("SMTP_TLS", "starttls")
    client = EmailClient()
    with patch("triage.notifications.email_client.smtplib.SMTP") as MockSMTP:
        client.send_email(["x@example.com"], "subject", "<p>hi</p>")
        # smtplib.SMTP.__enter__ returns self, so the call lands on the object
        # `smtplib.SMTP(...)` itself returned, not on a distinct `__enter__` mock.
        MockSMTP.return_value.starttls.assert_called_once()


def test_email_client_ssl_mode_uses_smtp_ssl(monkeypatch):
    monkeypatch.setenv("SMTP_TLS", "ssl")
    client = EmailClient()
    with patch("triage.notifications.email_client.smtplib.SMTP_SSL") as MockSMTPSSL:
        client.send_email(["x@example.com"], "subject", "<p>hi</p>")
        MockSMTPSSL.assert_called_once()


# --- Webhook: correct payload shapes + graceful failure ----------------------
def test_slack_webhook_payload_shape():
    client = WebhookClient()
    with patch("triage.notifications.webhook_client.requests.post") as mock_post:
        mock_post.return_value.raise_for_status.return_value = None
        client.send_slack_message("https://hooks.slack.test/x", "hello *world*")
        payload = mock_post.call_args.kwargs["json"]
        assert payload["text"] == "hello *world*"


def test_teams_webhook_payload_shape():
    client = WebhookClient()
    with patch("triage.notifications.webhook_client.requests.post") as mock_post:
        mock_post.return_value.raise_for_status.return_value = None
        client.send_teams_message("https://outlook.office.test/x", "Case done", "body text")
        payload = mock_post.call_args.kwargs["json"]
        assert payload["@type"] == "MessageCard"
        assert payload["summary"] == "Case done"


def test_webhook_failure_is_swallowed():
    client = WebhookClient()
    with patch("triage.notifications.webhook_client.requests.post", side_effect=Exception("boom")):
        client.send_slack_message("https://hooks.slack.test/x", "hi")  # must not raise


# --- Dispatcher: routes to the right client(s) -------------------------------
def test_dispatcher_routes_email_and_slack_only_when_requested():
    d = NotificationDispatcher()
    with patch.object(d.email_client, "send_email") as email_mock, patch.object(
        d.webhook_client, "send_slack_message"
    ) as slack_mock, patch.object(d.sms_client, "send_sms") as sms_mock:
        payload = NotificationPayload(
            types=[NotificationType.EMAIL, NotificationType.SLACK],
            subject="Case done",
            content="body",
            recipients=["x@example.com"],
            webhook_url="https://hooks.slack.test/x",
        )
        d.dispatch(payload)
        email_mock.assert_called_once()
        slack_mock.assert_called_once()
        sms_mock.assert_not_called()


def test_dispatcher_skips_email_without_recipients():
    d = NotificationDispatcher()
    with patch.object(d.email_client, "send_email") as email_mock:
        payload = NotificationPayload(types=[NotificationType.EMAIL], subject="s", content="c")
        d.dispatch(payload)
        email_mock.assert_not_called()


def test_dispatcher_skips_slack_without_webhook_url():
    d = NotificationDispatcher()
    with patch.object(d.webhook_client, "send_slack_message") as slack_mock:
        payload = NotificationPayload(types=[NotificationType.SLACK], subject="s", content="c")
        d.dispatch(payload)
        slack_mock.assert_not_called()
