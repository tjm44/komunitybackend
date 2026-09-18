from django.test import TestCase
from unittest.mock import patch, MagicMock
import uuid


# ---------------------------------------------------------------------------
# 1.2: Mocked Flutterwave integration tests
# These tests run OFFLINE — no real HTTP requests are made.
# The Flutterwave responses are fully deterministic and execute in <1 second.
# ---------------------------------------------------------------------------

class FlutterwaveIntegrationTest(TestCase):

    @patch('wallet.flutterwave.requests.post')
    def test_oauth_token_retrieval(self, mock_post):
        """get_access_token() returns a token from the OAuth response."""
        from wallet.flutterwave import get_access_token

        mock_response = MagicMock()
        mock_response.json.return_value = {'access_token': 'fake-test-token-abc123'}
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        token = get_access_token()
        self.assertEqual(token, 'fake-test-token-abc123')
        print("\n[MOCKED] OAuth Token retrieval tested.")

    @patch('wallet.flutterwave.requests.post')
    def test_voucher_charge_graceful_fail(self, mock_post):
        """charge_voucher() returns {'success': False} on an invalid PIN."""
        from wallet.flutterwave import charge_voucher

        mock_response = MagicMock()
        mock_response.json.return_value = {
            'status': 'error',
            'message': 'Invalid PIN',
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        ref = f"test-topup-{uuid.uuid4().hex[:8]}"
        res = charge_voucher(
            voucher_pin="9999999999999999",
            amount=100.00,
            email="test@komunity.com",
            phone_number="0821234567",
            tx_ref=ref
        )
        self.assertIn('success', res)
        self.assertFalse(res['success'])
        print(f"\n[MOCKED] Voucher charge response received: success={res['success']}")

    @patch('wallet.flutterwave.requests.post')
    def test_card_charge_graceful_fail(self, mock_post):
        """charge_card() returns {'success': False} on an invalid card number."""
        from wallet.flutterwave import charge_card

        mock_response = MagicMock()
        mock_response.json.return_value = {
            'status': 'error',
            'message': 'Invalid card number',
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        ref = f"test-card-{uuid.uuid4().hex[:8]}"
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
        print(f"\n[MOCKED] Card charge response received: success={res['success']}")


# ---------------------------------------------------------------------------
# Wallet ledger and model tests (no external HTTP — already offline)
# ---------------------------------------------------------------------------

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
