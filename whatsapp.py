To integrate WhatsApp into Komunity for sending automated notifications, transaction receipts, payment reminders, and group invites, there are two complementary approaches:

'''Backend Automated WhatsApp API (Server-Side): For automated system alerts, OTPs, payout approvals, payment reminders, and server-sent invites directly to a user's phone number.
Client-Side WhatsApp Deep-Link Sharing (Mobile/Web): For instant, free 1-tap sharing where group admins invite friends and contacts directly from their phone's WhatsApp.
Architecture Overview
┌─────────────────────────────────────────────────────────┐
│                      Komunity Flow                      │
├──────────────────────────┬──────────────────────────────┤
│ 1. Server-Side Auto API  │ Django Backend → Meta/Twilio │
│    (OTP, Reminders, Tx)  │ → WhatsApp Phone Number      │
├──────────────────────────┼──────────────────────────────┤
│ 2. One-Tap Member Invite │ Mobile App (React Native)    │
│    (Admins & Members)    │ → Opens Native WhatsApp App  │
└──────────────────────────┴──────────────────────────────┘
Approach 1: Server-Side WhatsApp API (Django Backend)
The official and most cost-effective solution is Meta’s WhatsApp Cloud API (or Twilio for WhatsApp for quick setup).

Meta provides 1,000 free service conversations per month, making it ideal for startups.

Step 1: Add WhatsApp Credentials to .env and settings.py
In komunitybackend/.env:
'''
ini
# Meta WhatsApp Cloud API
WHATSAPP_PHONE_NUMBER_ID=your_meta_phone_number_id
WHATSAPP_ACCESS_TOKEN=your_meta_permanent_access_token
WHATSAPP_BUSINESS_ACCOUNT_ID=your_waba_id
In komunitybackend/core/settings.py:

python
WHATSAPP_PHONE_NUMBER_ID = os.getenv('WHATSAPP_PHONE_NUMBER_ID', '')
WHATSAPP_ACCESS_TOKEN = os.getenv('WHATSAPP_ACCESS_TOKEN', '')
Step 2: Create a WhatsApp Service in Django
Create komunitybackend/user/whatsapp.py:

python
import logging
import requests
from django.conf import settings
logger = logging.getLogger(__name__)
META_WHATSAPP_URL = "https://graph.facebook.com/v19.0"
def format_phone_number(phone: str) -> str:
    """Ensure phone number is in international E.164 format without '+' or spaces."""
    cleaned = ''.join(filter(str.isdigit, phone))
    # If South African local format (082...), convert to 2782...
    if cleaned.startswith('0') and len(cleaned) == 10:
        cleaned = '27' + cleaned[1:]
    return cleaned
def send_whatsapp_template_message(to_phone: str, template_name: str, components: list = None, language_code: str = "en_US") -> bool:
    """
    Sends an official approved WhatsApp template message via Meta Cloud API.
    Required for initiating conversations outside the 24-hour customer service window.
    """
    phone_id = getattr(settings, 'WHATSAPP_PHONE_NUMBER_ID', None)
    access_token = getattr(settings, 'WHATSAPP_ACCESS_TOKEN', None)
    if not phone_id or not access_token:
        logger.warning(f"[WhatsApp Dev Mode] Message to {to_phone} with template '{template_name}' skipped (No API keys).")
        return False
    url = f"{META_WHATSAPP_URL}/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": format_phone_number(to_phone),
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
            logger.info(f"WhatsApp message successfully sent to {to_phone}")
            return True
        else:
            logger.error(f"WhatsApp API error ({response.status_code}): {response.text}")
            return False
    except Exception as e:
        logger.error(f"WhatsApp request failed: {e}")
        return False
def send_group_invite_whatsapp(to_phone: str, inviter_name: str, group_name: str, invite_link: str) -> bool:
    """
    Convenience function: Sends a group invitation via WhatsApp template.
    Template parameter variables: {{1}} = inviter_name, {{2}} = group_name, {{3}} = invite_link
    """
    components = [
        {
            "type": "body",
            "parameters": [
                {"type": "text", "text": inviter_name},
                {"type": "text", "text": group_name},
                {"type": "text", "text": invite_link}
            ]
        }
    ]
    return send_whatsapp_template_message(to_phone, template_name="komunity_group_invite", components=components)
def send_payment_reminder_whatsapp(to_phone: str, member_name: str, amount: str, group_name: str, due_date: str) -> bool:
    """
    Convenience function: Sends a contribution reminder.
    """
    components = [
        {
            "type": "body",
            "parameters": [
                {"type": "text", "text": member_name},
                {"type": "text", "text": amount},
                {"type": "text", "text": group_name},
                {"type": "text", "text": due_date}
            ]
        }
    ]
    return send_whatsapp_template_message(to_phone, template_name="komunity_payment_reminder", components=components)
Step 3: Trigger from Notifications or Group Events
You can hook this directly into komunitybackend/user/notifications.py so that high-priority notifications also trigger a WhatsApp message:

python
from .whatsapp import send_payment_reminder_whatsapp, send_group_invite_whatsapp
def dispatch_notification_to_user(user, title, message, notification_type=None, extra_data=None):
    # 1. Send In-App & Expo Push
    send_push_notification(user, title, message, data=extra_data, notification_type=notification_type)
    # 2. If WhatsApp notifications are enabled and user has phone number:
    if user.phone_number and notification_type == "CONTRIBUTION_DUE":
        send_payment_reminder_whatsapp(
            to_phone=user.phone_number,
            member_name=user.get_full_name() or user.username,
            amount=extra_data.get("amount", "0.00"),
            group_name=extra_data.get("group_name", "your group"),
            due_date=extra_data.get("due_date", "today")
        )
Approach 2: One-Tap "Invite via WhatsApp" in React Native App
# For inviting members from a user's phonebook, you don't need to pay API fees. You can use React Native's Linking API to open the native WhatsApp application with a pre-populated message and deep link.

Implementation in KomunityMobile:
Create a helper function src/utils/whatsappShare.ts:

typescript
import { Linking, Alert, Platform } from 'react-native';
interface WhatsAppInviteOptions {
    phone?: string; // Optional: if specified, opens chat directly with that number
    groupName: string;
    inviterName: string;
    inviteCodeOrLink: string;
}
export const shareToWhatsApp = async ({ phone, groupName, inviterName, inviteCodeOrLink }: WhatsAppInviteOptions) => {
    const message = `👋 Hi! ${inviterName} has invited you to join "${groupName}" on Komunity.\n\n` +
        `Komunity is a secure, transparent community wallet where you can track all contributions, savings, and payouts in real time.\n\n` +
        `👉 Tap here to join: ${inviteCodeOrLink}\n\n` +
        `End the money drama with Komunity!`;
    const encodedMessage = encodeURIComponent(message);
    let url = '';
    if (phone) {
        // Format phone: remove spaces and '+'
        const cleanedPhone = phone.replace(/[^0-9]/g, '');
        url = `https://wa.me/${cleanedPhone}?text=${encodedMessage}`;
    } else {
        // General WhatsApp share (user picks contact or WhatsApp group)
        url = Platform.select({
            ios: `whatsapp://send?text=${encodedMessage}`,
            android: `whatsapp://send?text=${encodedMessage}`,
            default: `https://wa.me/?text=${encodedMessage}`
        });
    }
    try {
        const supported = await Linking.canOpenURL(url);
        if (supported || Platform.OS === 'web') {
            await Linking.openURL(url);
        } else {
            Alert.alert(
                'WhatsApp Not Installed',
                'Please install WhatsApp on your device to share this invitation directly.'
            );
        }
    } catch (error) {
        console.error('Failed to open WhatsApp:', error);
        Alert.alert('Error', 'Unable to open WhatsApp.');
    }
};
Add the Button in GroupDetailScreen.tsx:
tsx
import { shareToWhatsApp } from '../utils/whatsappShare';
// Inside your component:
const handleWhatsAppInvite = () => {
    shareToWhatsApp({
        groupName: group.name,
        inviterName: 'John', // Current user's name
        inviteCodeOrLink: `https://komunity.app/join/${group.id}`
    });
};
// In JSX render:
<TouchableOpacity style={styles.whatsappButton} onPress={handleWhatsAppInvite}>
    <Ionicons name="logo-whatsapp" size={20} color="#FFFFFF" />
    <Text style={styles.whatsappButtonText}>Invite via WhatsApp</Text>
</TouchableOpacity>
How to Set Up Meta WhatsApp Cloud API (5 Steps)
If you want the server-side automated notifications:

Meta for Developers: Go to developers.facebook.com and create an account.
Create App: Click Create App → Select Business type → Add WhatsApp product.
Get Test Number & Token: Meta instantly gives you a test phone number and a temporary access token with 5 free test recipient numbers.
Create Message Templates:
In Meta Business Manager under WhatsApp Manager → Message Templates.
Create template komunity_group_invite (Category: Utility or Marketing).
Body text:
Hi {{1}}, you've been invited to join {{2}} on Komunity. Tap the link to view group activity and contributions: {{3}}
Templates are approved by Meta AI usually within 1 to 5 minutes.
Connect Production Number: Add your official company phone number and verify via OTP.
Comparison: When to use which?
Feature	Meta Cloud API (Backend)	1-Tap Client Link (wa.me)
Cost	1,000 free convos/mo, then ~R0.40 - R0.80 / msg	100% Free forever
Setup time	~30 mins (API credentials)	5 mins
Use cases	OTPs, automated monthly reminders, transaction receipts	User inviting their phonebook contacts or sharing to existing WhatsApp groups
Sender	Official Komunity Verified Number	The user's personal WhatsApp
Would you like me to implement the WhatsApp sharing button on the mobile screens or set up the Django backend WhatsApp notification helper in your codebase?

2:45 PM