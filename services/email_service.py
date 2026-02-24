import logging

from flask_mail import Message

from extensions import mail


def is_email_configured(app_config):
    """Check whether minimum SMTP configuration is present."""
    host = app_config.get("MAIL_SERVER", app_config.get("MAIL_HOST", ""))
    port = app_config.get("MAIL_PORT", 587)
    username = app_config.get("MAIL_USERNAME", "")
    password = app_config.get("MAIL_PASSWORD", "")
    sender = app_config.get("MAIL_FROM", username)
    return bool(host and port and username and password and sender)


def send_email(app_config, to_email, subject, body, html_body=None):
    """Send email through Flask-Mail. Supports optional HTML content."""
    host = app_config.get("MAIL_SERVER", app_config.get("MAIL_HOST", ""))
    port = app_config.get("MAIL_PORT", 587)
    username = app_config.get("MAIL_USERNAME", "")
    password = app_config.get("MAIL_PASSWORD", "")
    sender = app_config.get("MAIL_FROM", username)

    if not all([host, port, username, password, sender]):
        return False

    message = Message(
        subject=subject,
        recipients=[to_email],
        sender=("Let's Save Lives", sender),
        body=body,
    )
    if html_body:
        message.html = html_body

    try:
        mail.send(message)
        return True
    except Exception as email_error:
        logging.exception("Email sending failed: %s", email_error)
        return False
