import requests
from typing import Dict, Any

class WebhookClient:
    """
    Client for sending notifications to Slack or Teams webhooks.
    """
    
    def __init__(self):
        pass
        
    def send_slack_message(self, webhook_url: str, message: str, attachments: list = None):
        """
        Sends a message to a Slack webhook.
        """
        payload = {"text": message}
        if attachments:
            payload["attachments"] = attachments
            
        try:
            response = requests.post(webhook_url, json=payload)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"Failed to send Slack message: {e}")
            
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
        except requests.exceptions.RequestException as e:
            print(f"Failed to send Teams message: {e}")
