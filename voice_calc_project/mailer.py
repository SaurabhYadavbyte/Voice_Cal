"""Send single-use verification links over authenticated TLS SMTP."""

import os
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import quote, urlsplit


class MailConfigurationError(RuntimeError):
    pass


def mail_configured():
    origin = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    parsed = urlsplit(origin)
    return bool(
        parsed.scheme == "https"
        and parsed.hostname
        and not parsed.username
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
        and os.environ.get("SMTP_HOST")
        and os.environ.get("SMTP_USERNAME")
        and os.environ.get("SMTP_PASSWORD")
        and os.environ.get("SMTP_FROM", os.environ.get("SMTP_USERNAME"))
    )


def send_verification_email(recipient, token):
    if not mail_configured():
        raise MailConfigurationError("Verification email is not configured")

    port = int(os.environ.get("SMTP_PORT", "587"))
    if port not in (465, 587):
        raise MailConfigurationError("SMTP_PORT must be 465 or 587")

    origin = os.environ["PUBLIC_BASE_URL"].rstrip("/")
    link = f"{origin}/verify-email/{quote(token, safe='')}"
    message = EmailMessage()
    message["Subject"] = "Verify your VoiceCalc email"
    message["From"] = os.environ.get("SMTP_FROM") or os.environ["SMTP_USERNAME"]
    message["To"] = recipient
    message.set_content(
        "Thanks for joining VoiceCalc. Open the link below, then press Confirm email. "
        "The link expires in 30 minutes and can be used once.\n\n"
        f"{link}\n\nIf you did not request this account, ignore this email."
    )

    host = os.environ["SMTP_HOST"]
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=10, context=context) as smtp:
            smtp.login(os.environ["SMTP_USERNAME"], os.environ["SMTP_PASSWORD"])
            smtp.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            smtp.starttls(context=context)
            smtp.login(os.environ["SMTP_USERNAME"], os.environ["SMTP_PASSWORD"])
            smtp.send_message(message)
