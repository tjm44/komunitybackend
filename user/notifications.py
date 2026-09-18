import logging
try:
    from exponent_server_sdk import (
        PushClient, PushMessage, DeviceNotRegisteredError,
        PushServerError, PushTicketError
    )
except ImportError:
    PushClient = PushMessage = DeviceNotRegisteredError = PushServerError = PushTicketError = None
    print("Warning: exponent_server_sdk not found or failed to import. Push notifications will be disabled.")
from django.conf import settings
from .models import DeviceToken, Notification

logger = logging.getLogger(__name__)

def send_push_notification(user, title, message, data=None, notification_type=None):
    """
    Send push notification to a user's devices and store it in DB.
    Deactivates device tokens if Expo returns DeviceNotRegistered.
    """
    if data is None:
        data = {}

    # Store notification in DB
    Notification.objects.create(
        recipient=user,
        title=title,
        message=message,
        data=data,
        notification_type=notification_type
    )

    # Get active tokens
    tokens = list(DeviceToken.objects.filter(user=user, is_active=True).values_list('token', flat=True))
    
    if not tokens:
        return

    # Try offloading network I/O to django-q async task
    dispatched_async = False
    try:
        from django_q.tasks import async_task
        async_task(
            'user.tasks.task_deliver_push_and_whatsapp',
            user.id,
            tokens,
            title,
            message,
            data,
            notification_type
        )
        dispatched_async = True
    except Exception as exc:
        logger.debug(f"Async notification dispatch skipped ({exc}); delivering synchronously.")

    if not dispatched_async:
        _deliver_push_and_whatsapp_sync(user, tokens, title, message, data, notification_type)


def _deliver_push_and_whatsapp_sync(user, tokens, title, message, data=None, notification_type=None):
    """
    Synchronous fallback for push and WhatsApp delivery.
    """
    if tokens and PushClient:
        try:
            messages = [
                PushMessage(to=token, title=title, body=message, data=data or {})
                for token in tokens
            ]
            responses = PushClient().publish_multiple(messages)

            for token, response_ticket in zip(tokens, responses):
                try:
                    response_ticket.validate_response()
                except Exception as exc:
                    exc_str = str(exc)
                    if (DeviceNotRegisteredError and isinstance(exc, DeviceNotRegisteredError)) or "DeviceNotRegistered" in exc_str:
                        logger.info(f"Deactivating unregistered device token: {token}")
                        DeviceToken.objects.filter(token=token).update(is_active=False)
                    else:
                        logger.warning(f"Push notification ticket error for token {token}: {exc}")

        except Exception as exc:
            logger.error(f"Error sending push notification batch: {exc}")

    # Optional WhatsApp notification dispatch
    try:
        dispatch_whatsapp_notification(user, title, message, data=data, notification_type=notification_type)
    except Exception as exc:
        logger.error(f"Error dispatching WhatsApp notification: {exc}")


def dispatch_whatsapp_notification(user, title, message, data=None, notification_type=None):
    """
    Dispatches a WhatsApp notification to the user if a phone number is registered.
    Supports specific templates for reminders and group invites, and falls back to
    standard text notifications for other high-priority alerts.
    """
    if not user:
        return False

    if data is None:
        data = {}

    phone = getattr(user, 'phone', None)
    if not phone and hasattr(user, 'profile'):
        phone = getattr(user.profile, 'phone', None)

    if not phone:
        return False

    from .whatsapp import (
        send_payment_reminder_whatsapp,
        send_group_invite_whatsapp,
        send_whatsapp_text_message
    )

    n_type = (notification_type or "").lower()

    if n_type in ["contribution_due", "payment_reminder", "dues_reminder"]:
        member_name = ""
        if hasattr(user, 'profile') and user.profile:
            member_name = user.profile.full_name
        if not member_name:
            member_name = getattr(user, 'phone', 'Member')

        amount = str(data.get("amount", "0.00"))
        group_name = str(data.get("group_name", "your group"))
        due_date = str(data.get("due_date", "today"))

        return send_payment_reminder_whatsapp(
            to_phone=phone,
            member_name=member_name,
            amount=amount,
            group_name=group_name,
            due_date=due_date
        )

    elif n_type in ["group_invite", "invite"]:
        inviter_name = str(data.get("inviter_name", "A Komunity member"))
        group_name = str(data.get("group_name", "Komunity Group"))
        invite_link = str(data.get("invite_link", f"https://komunity.app/group/{data.get('group_id', '')}"))

        return send_group_invite_whatsapp(
            to_phone=phone,
            inviter_name=inviter_name,
            group_name=group_name,
            invite_link=invite_link
        )

    elif data.get("send_whatsapp", False) or n_type in ["membership_approved", "payout_processed", "transaction"]:
        body = f"*{title}*\n{message}"
        return send_whatsapp_text_message(to_phone=phone, message=body)

    return False


