from decimal import Decimal
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase, APIClient

from chema.models import Group, GroupMembership
from user.models import Profile
from wallet.models import Wallet, Transaction, GroupWalletTransferRequest

User = get_user_model()


class ApiV1FinancialAndGovernanceTests(APITestCase):
    def setUp(self):
        self.client = APIClient()

        # User A (Sender / Creator)
        self.user_a = User.objects.create_user(
            phone='0821111111',
            email='alice@komunity.app',
            password='Password123!',
            pin=make_password('1234'),
            is_phone_verified=True
        )
        self.profile_a = Profile.objects.get(user=self.user_a)
        self.wallet_a = Wallet.objects.get(user=self.user_a)
        Transaction.objects.create(
            wallet=self.wallet_a,
            transaction_type='TOP_UP',
            amount=Decimal('1000.00'),
            net_amount=Decimal('1000.00'),
            status='COMPLETED'
        )
        self.wallet_a.recalculate_balance()

        # User B (Recipient / Admin 2)
        self.user_b = User.objects.create_user(
            phone='0822222222',
            email='bob@komunity.app',
            password='Password123!',
            pin=make_password('5678'),
            is_phone_verified=True
        )
        self.profile_b = Profile.objects.get(user=self.user_b)
        self.wallet_b = Wallet.objects.get(user=self.user_b)
        Transaction.objects.create(
            wallet=self.wallet_b,
            transaction_type='TOP_UP',
            amount=Decimal('200.00'),
            net_amount=Decimal('200.00'),
            status='COMPLETED'
        )
        self.wallet_b.recalculate_balance()

        # User C (Admin 3)
        self.user_c = User.objects.create_user(
            phone='0823333333',
            email='charlie@komunity.app',
            password='Password123!',
            pin=make_password('9999'),
            is_phone_verified=True
        )
        self.profile_c = Profile.objects.get(user=self.user_c)

    # -----------------------------------------------------------------------
    # 1. P2P Wallet Transfers & Idempotency Tests
    # -----------------------------------------------------------------------

    def test_p2p_transfer_success(self):
        """Test successful P2P money transfer between members."""
        self.client.force_authenticate(user=self.user_a)
        url = reverse('wallet-send-money')

        payload = {
            'recipient_user_id': self.user_b.id,
            'amount': '150.00',
            'pin': '1234',
            'note': 'Grocery share'
        }
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.wallet_a.refresh_from_db()
        self.wallet_b.refresh_from_db()

        self.assertEqual(self.wallet_a.balance, Decimal('850.00'))
        self.assertEqual(self.wallet_b.balance, Decimal('350.00'))

        # Check transactions created
        self.assertTrue(Transaction.objects.filter(wallet=self.wallet_a, transaction_type='P2P_SENT').exists())
        self.assertTrue(Transaction.objects.filter(wallet=self.wallet_b, transaction_type='P2P_RECEIVED').exists())

    def test_p2p_transfer_idempotency_prevents_duplicate_debits(self):
        """Test that submitting duplicate requests with the same idempotency_key does NOT double-debit."""
        self.client.force_authenticate(user=self.user_a)
        url = reverse('wallet-send-money')

        payload = {
            'recipient_user_id': self.user_b.id,
            'amount': '100.00',
            'pin': '1234',
            'note': 'Idempotent test',
            'idempotency_key': 'IDEMP-P2P-TEST-KEY-001'
        }

        # 1st request
        res1 = self.client.post(url, payload, format='json')
        self.assertEqual(res1.status_code, status.HTTP_200_OK)
        self.wallet_a.refresh_from_db()
        self.assertEqual(self.wallet_a.balance, Decimal('900.00'))

        # 2nd request with exact same idempotency_key (e.g. network retry or button double-tap)
        res2 = self.client.post(url, payload, format='json')
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertTrue(res2.data.get('idempotent_replay'))

        # Balance must remain unchanged at 900.00 (NOT deducted twice)
        self.wallet_a.refresh_from_db()
        self.assertEqual(self.wallet_a.balance, Decimal('900.00'))

        # Only 1 P2P_SENT transaction should exist with this key
        sent_txns = Transaction.objects.filter(idempotency_key='IDEMP-P2P-TEST-KEY-001')
        self.assertEqual(sent_txns.count(), 1)

    def test_p2p_transfer_invalid_pin_rejected(self):
        """Test that transfer fails if wrong PIN is provided."""
        self.client.force_authenticate(user=self.user_a)
        url = reverse('wallet-send-money')

        payload = {
            'recipient_user_id': self.user_b.id,
            'amount': '50.00',
            'pin': '0000',  # Wrong PIN
        }
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # Balance unchanged
        self.wallet_a.refresh_from_db()
        self.assertEqual(self.wallet_a.balance, Decimal('1000.00'))

    def test_p2p_transfer_cannot_send_to_self(self):
        """Test sending to oneself is disallowed."""
        self.client.force_authenticate(user=self.user_a)
        url = reverse('wallet-send-money')

        payload = {
            'recipient_user_id': self.user_a.id,
            'amount': '50.00',
            'pin': '1234',
        }
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_p2p_transfer_insufficient_funds_rejected(self):
        """Test that transfer exceeding balance is rejected."""
        self.client.force_authenticate(user=self.user_a)
        url = reverse('wallet-send-money')

        payload = {
            'recipient_user_id': self.user_b.id,
            'amount': '5000.00',  # Exceeds 1000.00 balance
            'pin': '1234',
        }
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # -----------------------------------------------------------------------
    # 2. Group Admin Multi-Signature Disbursement Threshold Tests
    # -----------------------------------------------------------------------

    def test_group_multisig_disbursement_threshold(self):
        """Test multi-signature approval rules for group wallet transfers."""
        # Create group with 2 admin approvals required (creator User A + admin User B)
        group = Group.objects.create(
            name='Multi-Sig Stokvel',
            creator=self.user_a,
            min_disbursement_approvals=2
        )
        # Add Bob as admin (total admins: Alice & Bob = 2)
        group.admins.add(self.user_b)

        # Create incoming transaction so group has R1000 balance
        Transaction.objects.create(
            wallet=self.wallet_a,
            transaction_type='TRANSFER',
            amount=Decimal('1000.00'),
            net_amount=Decimal('1000.00'),
            status='COMPLETED',
            destination_group=group
        )
        self.assertEqual(group.get_balance(), Decimal('1000.00'))

        # Create transfer request for R400 to Bob
        transfer_req = GroupWalletTransferRequest.objects.create(
            group=group,
            requested_by=self.user_a,
            recipient_profile=self.profile_b,
            amount=Decimal('400.00'),
            status=GroupWalletTransferRequest.STATUS_PENDING
        )

        # Initially 0 approvals -> cannot execute
        self.assertFalse(transfer_req.can_execute())
        with self.assertRaises(ValueError):
            transfer_req.execute()

        # Admin A approves -> 1 approval (still needs 2)
        transfer_req.approvals.add(self.user_a)
        self.assertFalse(transfer_req.can_execute())

        # Admin B approves -> 2 approvals reached!
        transfer_req.approvals.add(self.user_b)
        self.assertTrue(transfer_req.can_execute())

        # Execute transfer
        transfer_req.execute()
        self.assertEqual(transfer_req.status, GroupWalletTransferRequest.STATUS_EXECUTED)

        # Recipient wallet should have received the funds (net of any fees)
        self.wallet_b.refresh_from_db()
        self.assertGreater(self.wallet_b.balance, Decimal('200.00'))
