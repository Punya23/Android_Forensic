import smtplib
from email.message import EmailMessage
from typing import List, Optional
import os

class EmailClient:
    """
    Client for sending email notifications using SMTP.
    """
    
    def __init__(self):
        self.smtp_host = os.environ.get('SMTP_HOST', 'localhost')
        self.smtp_port = int(os.environ.get('SMTP_PORT', 1025))
        self.smtp_user = os.environ.get('SMTP_USER', '')
        self.smtp_pass = os.environ.get('SMTP_PASS', '')
        self.sender = os.environ.get('SMTP_SENDER', 'erakshak@example.com')
        
    def send_email(self, recipients: List[str], subject: str, html_content: str, text_content: Optional[str] = None):
        """
        Sends an email to the specified recipients.
        """
        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = self.sender
        msg['To'] = ', '.join(recipients)
        
        if text_content:
            msg.set_content(text_content)
            msg.add_alternative(html_content, subtype='html')
        else:
            msg.set_content(html_content, subtype='html')
            
        with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
            if self.smtp_user and self.smtp_pass:
                server.login(self.smtp_user, self.smtp_pass)
            server.send_message(msg)
