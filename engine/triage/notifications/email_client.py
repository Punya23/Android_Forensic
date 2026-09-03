import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import List, Optional
import os

logger = logging.getLogger(__name__)


class EmailClient:
    """
    Client for sending email notifications using SMTP.

    Defaults to a local unauthenticated relay (``localhost:1025``, MailHog-style) for
    development. ``SMTP_TLS=starttls`` or ``SMTP_TLS=ssl`` opt into encryption for a
    real provider (Gmail, SendGrid, etc. require one of the two) — plain SMTP with no
    encryption flag set is left as the default so a local dev relay keeps working with
    no extra configuration, but it will fail against any provider that requires
    encrypted submission, which is what most of them do.
    """

    def __init__(self):
        self.smtp_host = os.environ.get('SMTP_HOST', 'localhost')
        self.smtp_port = int(os.environ.get('SMTP_PORT', 1025))
        self.smtp_user = os.environ.get('SMTP_USER', '')
        self.smtp_pass = os.environ.get('SMTP_PASS', '')
        self.sender = os.environ.get('SMTP_SENDER', 'snagr@example.com')
        # "" (default) | "starttls" | "ssl"
        self.tls_mode = os.environ.get('SMTP_TLS', '').strip().lower()

    def send_email(self, recipients: List[str], subject: str, html_content: str, text_content: Optional[str] = None):
        """
        Sends an email to the specified recipients. Never raises — a failed send is
        logged and swallowed, matching the graceful-degradation pattern the SMS and
        webhook clients already use, so one failed notification channel can't take
        down the acquisition it's reporting on.
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

        try:
            if self.tls_mode == 'ssl':
                server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, context=ssl.create_default_context())
            else:
                server = smtplib.SMTP(self.smtp_host, self.smtp_port)
            with server:
                if self.tls_mode == 'starttls':
                    server.starttls(context=ssl.create_default_context())
                if self.smtp_user and self.smtp_pass:
                    server.login(self.smtp_user, self.smtp_pass)
                server.send_message(msg)
        except Exception as e:
            logger.warning("Failed to send email to %s: %s", recipients, e)
