import logging
from wallet.flutterwave import verify_identity

logger = logging.getLogger(__name__)


class FlutterwaveKYCProvider:
    @staticmethod
    def verify_document(first_name, surname, id_number, id_type='national_id'):
        """
        Verifies identity documents using Flutterwave's Identity Verification API.
        Includes validation rules and sandbox fallback for testing.
        """
        if not id_number or len(id_number.strip()) < 6:
            return False, "Invalid document ID: ID must be at least 6 characters long.", {}

        id_cleaned = id_number.strip().lower()
        if id_cleaned in ("000000", "test", "123456"):
            return False, "KYC provider rejected document: potential fake or testing ID.", {}

        # Call Flutterwave identity API
        res = verify_identity(
            id_number=id_number.strip(),
            id_type=id_type,
            first_name=first_name,
            surname=surname
        )

        if res.get('success'):
            data = res.get('data') or {}
            if not data.get('first_name') and first_name:
                data['first_name'] = first_name
            if not data.get('last_name') and surname:
                data['last_name'] = surname
            return True, res.get('message', 'Identity verified successfully.'), data
        
        # If sandbox mode returns endpoint unavailable, auth failure, or invalid mock ID in dev, fallback gracefully for valid formatted IDs
        from django.conf import settings
        error_msg = res.get('error', '')
        if getattr(settings, 'DEBUG', False):
            logger.info(f"[KYC] Sandbox fallback triggered for valid ID format in DEBUG mode (upstream error: {error_msg}).")
            data = {'first_name': first_name, 'last_name': surname}
            return True, "Identity verified successfully (Sandbox Mode).", data

        return False, error_msg, {}


# Alias for backwards compatibility
MockKYCProvider = FlutterwaveKYCProvider
