# Komunity WhatsApp Integration Guide

This document outlines the architecture and setup for WhatsApp integration within Komunity for sending automated notifications, transaction receipts, payment reminders, and 1-tap group invitations.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                      Komunity Flow                      │
├──────────────────────────┬──────────────────────────────┤
│ 1. Server-Side Auto API  │ Django Backend → Meta Cloud  │
│    (OTP, Reminders, Tx)  │ → WhatsApp Phone Number      │
├──────────────────────────┼──────────────────────────────┤
│ 2. One-Tap Member Invite │ Mobile App (React Native)    │
│    (Admins & Members)    │ → Opens Native WhatsApp App  │
└──────────────────────────┴──────────────────────────────┘
```

### Approach 1: Server-Side WhatsApp API (Django Backend)
- **Use cases**: System alerts, payment reminders, transaction receipts, automated reminders.
- **Provider**: Meta WhatsApp Cloud API (Graph API v19.0).
- **Cost**: 1,000 free service conversations per month.
- **Dev Mode**: If keys are omitted in `.env`, notifications are logged to console/logger without breaking request flows.

### Approach 2: One-Tap WhatsApp Invite Sharing (React Native App)
- **Use cases**: Group admins and members inviting contacts or sharing to existing WhatsApp group chats.
- **Mechanism**: React Native `Linking` API (`whatsapp://send?text=...` / `https://wa.me/...`).
- **Cost**: 100% Free forever (uses user's personal WhatsApp).

---

## Meta WhatsApp Cloud API Setup (5 Steps)

1. **Meta for Developers**: Go to [developers.facebook.com](https://developers.facebook.com/) and register / log in.
2. **Create App**: Click **Create App** → Select **Business** type → Add the **WhatsApp** product.
3. **Get Test Number & Token**: Meta provides a test phone number and a temporary access token with up to 5 test recipient numbers for immediate testing.
4. **Create Message Templates**:
   In Meta Business Manager under **WhatsApp Manager → Message Templates**:
   - `komunity_group_invite` (Utility/Marketing):
     > Hi {{1}}, you've been invited to join {{2}} on Komunity. Tap the link to view group activity and contributions: {{3}}
   - `komunity_payment_reminder` (Utility):
     > Hi {{1}}, friendly reminder that your contribution of R{{2}} for {{3}} is due on {{4}}. View details on Komunity.
5. **Add Environment Variables**:
   In `komunitybackend/.env`:
   ```ini
   WHATSAPP_PHONE_NUMBER_ID=your_phone_number_id
   WHATSAPP_ACCESS_TOKEN=your_meta_system_user_token
   WHATSAPP_BUSINESS_ACCOUNT_ID=your_waba_id
   ```
