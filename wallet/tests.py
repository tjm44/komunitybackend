from django.test import TestCase
from wallet.flutterwave import get_access_token, charge_voucher, charge_card, initiate_transfer
import uuid

class FlutterwaveIntegrationTest(TestCase):
    def test_oauth_token_retrieval(self):
        try:
            token = get_access_token()
            self.assertIsNotNone(token)
            self.assertTrue(len(token) > 0)
            print("\n[SUCCESS] OAuth Token retrieved from Flutterwave Sandbox!")
        except Exception as e:
            self.fail(f"OAuth Token retrieval failed: {e}")

    def test_voucher_charge_graceful_fail(self):
        # We test with an invalid pin to make sure the endpoint receives our request
        # and returns a structured validation/auth response rather than crashing.
        ref = f"test-topup-{uuid.uuid4().hex[:8]}"
        res = charge_voucher(
            voucher_pin="9999999999999999",  # Mock invalid pin
            amount=100.00,
            email="test@komunity.com",
            phone_number="0821234567",
            tx_ref=ref
        )
        self.assertIn('success', res)
        print(f"\n[SUCCESS] Voucher charge response received: success={res['success']}")

    def test_card_charge_graceful_fail(self):
        ref = f"test-card-{uuid.uuid4().hex[:8]}"
        # Test with invalid card number in sandbox
        res = charge_card(
            card_number="1111222233334444",
            expiry_month="12",
            expiry_year="30",
            cvv="123",
            amount=50.00,
            email="test@komunity.com",
            phone_number="0821234567",
            tx_ref=ref
        )
        self.assertIn('success', res)
        print(f"\n[SUCCESS] Card charge response received: success={res['success']}")

from django.contrib.auth import get_user_model
from wallet.models import Wallet, Transaction
from wallet.webhooks import handle_charge_completed

User = get_user_model()

class WalletLedgerResilienceTest(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(email="user1@example.com", password="password123")
        self.user2 = User.objects.create_user(email="user2@example.com", password="password123")
        self.wallet1 = Wallet.objects.get(user=self.user1)
        self.wallet2 = Wallet.objects.get(user=self.user2)

    def test_denormalized_balance_and_webhook(self):
        self.assertEqual(self.wallet1.balance, 0.00)

        # Create pending deposit transaction
        tx_ref = "test-deposit-100"
        tx = Transaction.objects.create(
            wallet=self.wallet1,
            transaction_type=Transaction.TransactionType.TOP_UP,
            amount=100.00,
            status=Transaction.TransactionStatus.PENDING,
            idempotency_key=tx_ref
        )

        # Simulate webhook payload
        data = {
            'reference': tx_ref,
            'amount': 100.00,
            'status': 'successful',
            'id': 'flw-123456'
        }

        response = handle_charge_completed(data)
        self.assertEqual(response.status_code, 200)

        self.wallet1.refresh_from_db()
        tx.refresh_from_db()

        self.assertEqual(self.wallet1.balance, 100.00)
        self.assertEqual(tx.status, Transaction.TransactionStatus.COMPLETED)

        # Test Idempotency: Repeating webhook call shouldn't double credit
        response_repeat = handle_charge_completed(data)
        self.assertEqual(response_repeat.status_code, 200)

        self.wallet1.refresh_from_db()
        self.assertEqual(self.wallet1.balance, 100.00)


class SavedCardFeatureTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="cardholder@example.com", password="password123")
        self.wallet = Wallet.objects.get(user=self.user)

    def test_detect_card_brand(self):
        from wallet.flutterwave import detect_card_brand
        self.assertEqual(detect_card_brand("4111 2222 3333 4444"), "Visa")
        self.assertEqual(detect_card_brand("5531886652142950"), "Mastercard")
        self.assertEqual(detect_card_brand("378282246310005"), "American Express")
        self.assertEqual(detect_card_brand("6011000990139424"), "Discover")

    def test_saved_card_model_creation_and_scoping(self):
        from wallet.models import SavedCard
        card = SavedCard.objects.create(
            user=self.user,
            customer_id="flw_cust_123",
            payment_method_id="flw_pm_456",
            card_brand="Visa",
            last4="4242",
            expiry_month="12",
            expiry_year="2028",
            is_default=True
        )
        self.assertEqual(card.last4, "4242")
        self.assertTrue(card.is_default)
        self.assertEqual(SavedCard.objects.filter(user=self.user).count(), 1)
        self.assertIn("Visa •••• 4242", str(card))

    def test_saved_card_serializer_excludes_sensitive_tokens(self):
        from wallet.models import SavedCard
        from wallet.serializers import SavedCardSerializer
        card = SavedCard.objects.create(
            user=self.user,
            customer_id="secret_customer_token",
            payment_method_id="secret_pm_token",
            card_brand="Mastercard",
            last4="5555",
            expiry_month="08",
            expiry_year="2027",
            is_default=True
        )
        data = SavedCardSerializer(card).data
        self.assertIn("last4", data)
        self.assertIn("card_brand", data)
        self.assertNotIn("customer_id", data)
        self.assertNotIn("payment_method_id", data)
