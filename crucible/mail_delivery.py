"""Small explicit mail transport; secrets are never returned to HTTP callers."""
from email.message import EmailMessage
import os
from pathlib import Path
import smtplib
import ssl
from uuid import uuid4


class DeliveryUnavailable(RuntimeError):
    pass


def send_verification(settings, recipient, code):
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = recipient
    message["Subject"] = "Your Crucible invitation verification code"
    message.set_content(
        f"Your Crucible verification code is: {code}\n\n"
        "This code expires in 10 minutes and confirms the email address on your invitation. "
        "Enter it only in the Crucible page where you requested it. "
        "If you did not request this code, ignore this message.\n"
    )
    _deliver(settings, message)


def send_password_reset(settings, recipient, url):
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = recipient
    message["Subject"] = "Reset your Crucible password"
    message.set_content(
        "Use this link to reset your Crucible password:\n\n" + url + "\n\n"
        "This link expires in 30 minutes and works once. Resetting your password "
        "signs out your browsers and connected agents. Your contributions and "
        "linked sign-in accounts are preserved.\n\n"
        "If you did not request a reset, ignore this message. Your password has not changed.\n"
    )
    _deliver(settings, message)


def _deliver(settings, message):
    backend = getattr(settings, "mail_backend", "disabled")
    try:
        if backend == "local" and not settings.production:
            directory = Path(settings.mail_outbox_dir).expanduser()
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            # O_EXCL prevents replacing files or following a pre-existing symlink.
            path = directory / (str(uuid4()) + ".eml")
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(message.as_bytes())
            return
        if backend == "smtp" and settings.smtp_starttls:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as client:
                client.ehlo()
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
                if settings.smtp_username:
                    client.login(settings.smtp_username, settings.smtp_password)
                client.send_message(message)
            return
    except (OSError, ValueError, smtplib.SMTPException):
        # SMTP diagnostics can contain recipients or authentication material.
        raise DeliveryUnavailable("Verification email could not be delivered. Please contact the operator.") from None
    raise DeliveryUnavailable("Verification email is not configured. Please contact the operator.")
