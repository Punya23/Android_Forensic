import logging
import requests
from typing import Dict, Any

logger = logging.getLogger(__name__)


class WebhookClient:
    """
    Client for sending notifications to Slack or Teams webhooks.

    Note on Teams: the payload here is the legacy "MessageCard" connector format,
    which Microsoft retired industry-wide during 2025 in favour of Adaptive Cards via
    Power Automate workflows. It may no longer deliver to a current Teams webhook —
    verify against a live one before relying on it; Slack/email are the safer default.
    """

    def __init__(self):
        pass

    def send_slack_message(self, webhook_url: str, message: str, attachments: list = None):
        """
        Sends a message to a Slack webhook. Never raises — see module note on the
        broad except: a caller reporting on a completed acquisition must not have that
        success turned into a failure by an unrelated webhook error.
        """
        payload = {"text": message}
        if attachments:
            payload["attachments"] = attachments

        try:
            response = requests.post(webhook_url, json=payload)
            response.raise_for_status()
        except Exception as e:
            logger.warning("Failed to send Slack message: %s", e)

    def send_teams_message(self, webhook_url: str, title: str, text: str):
        """
        Sends a message to a Microsoft Teams webhook.
        """
        payload = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": "0076D7",
            "summary": title,
            "sections": [{
                "activityTitle": title,
                "text": text
            }]
        }

        try:
            response = requests.post(webhook_url, json=payload)
            response.raise_for_status()
        except Exception as e:
            logger.warning("Failed to send Teams message: %s", e)
