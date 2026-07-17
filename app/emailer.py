"""SMTP delivery for the morning digest. Credentials come from the
environment (.env) — never hardcode them."""
import os
import smtplib
import ssl
from email.message import EmailMessage


def is_configured() -> bool:
    return all(
        os.environ.get(k)
        for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "DIGEST_TO")
    )


def send(subject: str, html: str, text: str) -> None:
    if not is_configured():
        raise RuntimeError(
            "SMTP not configured — set SMTP_HOST, SMTP_PORT, SMTP_USER, "
            "SMTP_PASS and DIGEST_TO (see .env.example)"
        )
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["DIGEST_TO"]
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    port = int(os.environ.get("SMTP_PORT", "587"))
    with smtplib.SMTP(os.environ["SMTP_HOST"], port, timeout=30) as server:
        server.starttls(context=ssl.create_default_context())
        server.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        server.send_message(msg)
