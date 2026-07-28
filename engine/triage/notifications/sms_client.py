import os
from twilio.rest import Client
from typing import List

class SMSClient:
    """
    Client for sending SMS notifications using Twilio.
    """
    
    def __init__(self):
        self.account_sid = os.environ.get('TWILIO_ACCOUNT_SID', '')
        self.auth_token = os.environ.get('TWILIO_AUTH_TOKEN', '')
        self.from_number = os.environ.get('TWILIO_FROM_NUMBER', '')
        
        if self.account_sid and self.auth_token:
            self.client = Client(self.account_sid, self.auth_token)
        else:
            self.client = None
            
    def send_sms(self, recipients: List[str], message: str):
        """
        Sends an SMS to the specified recipients.
        """
        if not self.client:
            print(f"Warning: Twilio not configured. Would have sent SMS to {recipients}: {message}")
            return
            
        for to_number in recipients:
            try:
                self.client.messages.create(
                    body=message,
                    from_=self.from_number,
                    to=to_number
                )
            except Exception as e:
                print(f"Failed to send SMS to {to_number}: {e}")
