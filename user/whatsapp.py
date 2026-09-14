import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

META_WHATSAPP_URL = "https://graph.facebook.com/v19.0"


def format_whatsapp_phone(phone: str) -> str:
    """
    Format a phone number for the Meta WhatsApp Cloud API.
    Removes '+', spaces, dashes, and converts local South African numbers (08x...) to 278x...
    """
    if not phone:
        return ""
    cleaned = ''.join(filter(str.isdigit, str(phone)))
    # If South African local format (082... - 10 digits), convert to international 2782...
    if cleaned.startswith('0') and len(cleaned) == 10:
        cleaned = '27' + cleaned[1:]
    return cleaned


def send_whatsapp_template_message(
    to_phone: str,
    template_name: str,
    components: list = None,
    language_code: str = "en_US"
) -> bool:
    """
    Sends an approved WhatsApp template message via Meta Cloud API.
    Required for initiating business-to-consumer conversations.
    """
    formatted_phone = format_whatsapp_phone(to_phone)
    if not formatted_phone:
        logger.warning("[WhatsApp] Cannot send message: invalid or empty phone number.")
        return False

    phone_id = getattr(settings, 'WHATSAPP_PHONE_NUMBER_ID', '')
    access_token = getattr(settings, 'WHATSAPP_ACCESS_TOKEN', '')

    # Dev/Sandbox fallback when credentials are not configured
    if not phone_id or not access_token:
        logger.info(
            f"[WhatsApp Dev Mode] Simulated template '{template_name}' to {formatted_phone} (no API credentials set)."
        )
        if getattr(settings, 'DEBUG', False):
            print(f"\n==========================================")
            print(f"[DEV WHATSAPP ENGINE] Template: {template_name} -> {formatted_phone}")
            print(f"Components: {components}")
            print(f"==========================================\n")
        return True

    url = f"{META_WHATSAPP_URL}/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": formatted_phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {
                "code": language_code
            },
            "components": components or []
        }
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code in [200, 201]:
            logger.info(f"[WhatsApp] Template '{template_name}' successfully sent to {formatted_phone}")
            return True
        else:
            logger.error(f"[WhatsApp] API error ({response.status_code}): {response.text}")
            return False
    except Exception as e:
        logger.error(f"[WhatsApp] Request failed: {e}")
        return False


def send_whatsapp_text_message(to_phone: str, message: str) -> bool:
    """
    Sends a free-form WhatsApp text message via Meta Cloud API.
    Note: Standard text messages can only be delivered within an active 24-hour customer care window.
    """
    formatted_phone = format_whatsapp_phone(to_phone)
    if not formatted_phone:
        return False

    phone_id = getattr(settings, 'WHATSAPP_PHONE_NUMBER_ID', '')
    access_token = getattr(settings, 'WHATSAPP_ACCESS_TOKEN', '')

    if not phone_id or not access_token:
        logger.info(f"[WhatsApp Dev Mode] Text message to {formatted_phone}: {message}")
        if getattr(settings, 'DEBUG', False):
            print(f"[DEV WHATSAPP ENGINE] Text message to {formatted_phone}: {message}")
        return True

    url = f"{META_WHATSAPP_URL}/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": formatted_phone,
        "type": "text",
        "text": {
            "preview_url": True,
            "body": message
        }
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code in [200, 201]:
            logger.info(f"[WhatsApp] Text message successfully sent to {formatted_phone}")
            return True
        else:
            logger.error(f"[WhatsApp] Text API error ({response.status_code}): {response.text}")
            return False
    except Exception as e:
        logger.error(f"[WhatsApp] Text request failed: {e}")
        return False


def send_group_invite_whatsapp(
    to_phone: str,
    inviter_name: str,
    group_name: str,
    invite_link: str
) -> bool:
    """
    Convenience function: Sends a group invitation via WhatsApp template.
    Template parameter variables: {{1}} = inviter_name, {{2}} = group_name, {{3}} = invite_link
    """
    components = [
        {
            "type": "body",
            "parameters": [
                {"type": "text", "text": str(inviter_name or "A friend")},
                {"type": "text", "text": str(group_name or "Komunity Group")},
                {"type": "text", "text": str(invite_link or "https://komunity.app")}
            ]
        }
    ]
    return send_whatsapp_template_message(
        to_phone=to_phone,
        template_name="komunity_group_invite",
        components=components
    )


def send_payment_reminder_whatsapp(
    to_phone: str,
    member_name: str,
    amount: str,
    group_name: str,
    due_date: str
) -> bool:
    """
    Convenience function: Sends a contribution reminder via WhatsApp template.
    Template parameter variables: {{1}} = member_name, {{2}} = amount, {{3}} = group_name, {{4}} = due_date
    """
    components = [
        {
            "type": "body",
            "parameters": [
                {"type": "text", "text": str(member_name or "Member")},
                {"type": "text", "text": str(amount or "0.00")},
                {"type": "text", "text": str(group_name or "your group")},
                {"type": "text", "text": str(due_date or "today")}
            ]
        }
    ]
    return send_whatsapp_template_message(
        to_phone=to_phone,
        template_name="komunity_payment_reminder",
        components=components
    )
