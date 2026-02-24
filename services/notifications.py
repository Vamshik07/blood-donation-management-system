import logging

try:
    from twilio.rest import Client
except Exception:
    Client = None


def send_sms_update(phone_number, message, app_config):
    """Send SMS notification if Twilio is configured; otherwise log and skip."""
    if not phone_number:
        return False

    account_sid = app_config.get("TWILIO_ACCOUNT_SID", "")
    auth_token = app_config.get("TWILIO_AUTH_TOKEN", "")
    from_number = app_config.get("TWILIO_FROM_NUMBER", "")

    if not all([account_sid, auth_token, from_number]) or Client is None:
        logging.info("SMS not sent: provider not configured. Phone=%s Message=%s", phone_number, message)
        return False

    try:
        client = Client(account_sid, auth_token)
        client.messages.create(
            body=message,
            from_=from_number,
            to=phone_number,
        )
        return True
    except Exception as sms_error:
        logging.exception("SMS sending failed: %s", sms_error)
        return False
