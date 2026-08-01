from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel

from .email_client import EmailClient
from .sms_client import SMSClient
from .webhook_client import WebhookClient

class NotificationType(str, Enum):
    EMAIL = "email"
    SMS = "sms"
    SLACK = "slack"
    TEAMS = "teams"

class NotificationPayload(BaseModel):
    types: List[NotificationType]
    subject: str
    content: str
    recipients: Optional[List[str]] = None
    webhook_url: Optional[str] = None
    
class NotificationDispatcher:
    """
    Central dispatcher that routes notifications to the appropriate channels.
    """
    
    def __init__(self):
        self.email_client = EmailClient()
        self.sms_client = SMSClient()
        self.webhook_client = WebhookClient()
        
    def dispatch(self, payload: NotificationPayload):
        """
        Dispatches notifications to all requested channels based on rules.
        """
        for n_type in payload.types:
            if n_type == NotificationType.EMAIL and payload.recipients:
                self.email_client.send_email(
                    recipients=payload.recipients,
                    subject=payload.subject,
                    html_content=f"<p>{payload.content}</p>",
                    text_content=payload.content
                )
            elif n_type == NotificationType.SMS and payload.recipients:
                self.sms_client.send_sms(
                    recipients=payload.recipients,
                    message=f"{payload.subject}: {payload.content}"
                )
            elif n_type == NotificationType.SLACK and payload.webhook_url:
                self.webhook_client.send_slack_message(
                    webhook_url=payload.webhook_url,
                    message=f"*{payload.subject}*\n{payload.content}"
                )
            elif n_type == NotificationType.TEAMS and payload.webhook_url:
                self.webhook_client.send_teams_message(
                    webhook_url=payload.webhook_url,
                    title=payload.subject,
                    text=payload.content
                )
