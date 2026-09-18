from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status
from user.models import PhoneOTP, CustomUser

class PhoneOTPTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_request_otp_success(self):
        url = reverse('request_otp')
        response = self.client.post(url, {'phone': '+254712345678'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('phone', response.data)
        self.assertTrue(PhoneOTP.objects.filter(phone='+254712345678').exists())

    def test_verify_otp_creates_user(self):
        # 1. Request OTP
        url_req = reverse('request_otp')
        res_req = self.client.post(url_req, {'phone': '+254700112233'}, format='json')
        otp_code = PhoneOTP.objects.filter(phone='+254700112233').latest('created_at').otp

        # 2. Verify OTP
        url_ver = reverse('verify_otp')
        res_ver = self.client.post(url_ver, {'phone': '+254700112233', 'otp': otp_code}, format='json')
        self.assertEqual(res_ver.status_code, status.HTTP_200_OK)
        self.assertIn('token', res_ver.data)
        self.assertTrue(res_ver.data.get('is_new_user'))
    def test_set_and_verify_pin(self):
        # 1. Create user
        user = CustomUser.objects.create_user(phone='+254799887766')
        self.assertFalse(user.has_pin)

        # 2. Set PIN (requires authenticated user)
        self.client.force_authenticate(user=user)
        url_set = reverse('set_pin')
        res_set = self.client.post(url_set, {'phone': '+254799887766', 'pin': '4321'}, format='json')
        self.assertEqual(res_set.status_code, status.HTTP_200_OK)

        user.refresh_from_db()
        self.assertTrue(user.has_pin)

        # Logout before testing verify_pin (which is an unauthenticated endpoint)
        self.client.force_authenticate(user=None)

        # 3. Verify incorrect PIN
        url_ver_pin = reverse('verify_pin')
        res_fail = self.client.post(url_ver_pin, {'phone': '+254799887766', 'pin': '0000'}, format='json')
        self.assertEqual(res_fail.status_code, status.HTTP_400_BAD_REQUEST)

        # 4. Verify correct PIN
        res_win = self.client.post(url_ver_pin, {'phone': '+254799887766', 'pin': '4321'}, format='json')
        self.assertEqual(res_win.status_code, status.HTTP_200_OK)
        self.assertIn('token', res_win.data)


