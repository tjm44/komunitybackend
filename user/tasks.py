"""
komunitybackend/user/tasks.py
==============================
Django-Q2 async task wrappers for all external I/O operations.

Usage (from any view/signal):
    from django_q.tasks import async_task
    from user.tasks import task_deliver_push_and_whatsapp

    # Fire-and-forget — returns immediately, processed by qcluster
    async_task(task_deliver_push_and_whatsapp, user.id, tokens, "Title", "Message", data)

Worker startup:
    python manage.py qcluster
"""
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Push & WhatsApp Network Delivery Task
# ---------------------------------------------------------------------------

def task_deliver_push_and_whatsapp(user_id: int, tokens: list, title: str,
                                   message: str, data: dict | None = None,
                                   notification_type: str | None = None):
    """
    Async background task: handles external network calls for Expo Push and WhatsApp.
    Takes tokens directly or fetches user from DB to keep the HTTP request handler fast.
    """
    from django.contrib.auth import get_user_model
    from user.models import DeviceToken
    try:
        from exponent_server_sdk import PushClient, PushMessage, DeviceNotRegisteredError
    except ImportError:
        PushClient = PushMessage = DeviceNotRegisteredError = None

    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning(f"[task_deliver_push_and_whatsapp] User {user_id} not found, skipping.")
        return

    # 1. Deliver Expo Push notifications
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
                        logger.info(f"[task_deliver_push_and_whatsapp] Deactivating unregistered token: {token}")
                        DeviceToken.objects.filter(token=token).update(is_active=False)
                    else:
                        logger.warning(f"[task_deliver_push_and_whatsapp] Push ticket error for token {token}: {exc}")
        except Exception as exc:
            logger.error(f"[task_deliver_push_and_whatsapp] Push publish error: {exc}")

    # 2. Dispatch WhatsApp notification
    try:
        from user.notifications import dispatch_whatsapp_notification
        dispatch_whatsapp_notification(user, title, message, data=data, notification_type=notification_type)
    except Exception as exc:
        logger.error(f"[task_deliver_push_and_whatsapp] WhatsApp dispatch error: {exc}")


def task_send_push_notification(user_id: int, title: str, message: str,
                                 data: dict | None = None, notification_type: str | None = None):
    """
    Full async task: stores notification in DB, then dispatches Push and WhatsApp.
    """
    from django.contrib.auth import get_user_model
    from user.notifications import send_push_notification

    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
        send_push_notification(user, title, message, data=data or {}, notification_type=notification_type)
    except User.DoesNotExist:
        logger.warning(f"[task_send_push_notification] User {user_id} not found, skipping.")
    except Exception as exc:
        logger.error(f"[task_send_push_notification] Failed for user {user_id}: {exc}", exc_info=True)
        raise


# ---------------------------------------------------------------------------
# SMS Task
# ---------------------------------------------------------------------------

def task_send_sms(phone_number: str, message: str):
    """
    Async task: send an SMS via the configured BulkSMS gateway.
    """
    from user.sms import send_sms

    try:
        send_sms(phone_number, message)
    except Exception as exc:
        logger.error(f"[task_send_sms] Failed to {phone_number}: {exc}", exc_info=True)
        raise


def task_send_otp_sms(phone: str, otp: str):
    """
    Async task: send OTP SMS via BulkSMS API.
    """
    from user.sms import send_otp_sms

    try:
        send_otp_sms(phone, otp)
    except Exception as exc:
        logger.error(f"[task_send_otp_sms] Failed for {phone}: {exc}", exc_info=True)
        raise


# ---------------------------------------------------------------------------
# WhatsApp Task
# ---------------------------------------------------------------------------

def task_send_whatsapp_message(phone_number: str, message: str):
    """
    Async task: send a WhatsApp Cloud API message.
    """
    from user.whatsapp import send_whatsapp_message

    try:
        send_whatsapp_message(phone_number, message)
    except Exception as exc:
        logger.error(f"[task_send_whatsapp_message] Failed to {phone_number}: {exc}", exc_info=True)
        raise


# ---------------------------------------------------------------------------
# Flutterwave — Payment verification callbacks
# ---------------------------------------------------------------------------

def task_verify_flutterwave_transaction(tx_ref: str, flw_ref: str):
    """
    Async task: verify a Flutterwave transaction status after a webhook event.
    Calls the Flutterwave verify endpoint and updates the Transaction record.
    """
    from wallet.flutterwave import verify_transaction
    from wallet.models import Transaction

    try:
        result = verify_transaction(tx_ref, flw_ref)
        logger.info(f"[task_verify_flutterwave_transaction] tx_ref={tx_ref} result={result}")
    except Transaction.DoesNotExist:
        logger.warning(f"[task_verify_flutterwave_transaction] Transaction {tx_ref} not found.")
    except Exception as exc:
        logger.error(f"[task_verify_flutterwave_transaction] Failed for tx_ref={tx_ref}: {exc}", exc_info=True)
        raise
