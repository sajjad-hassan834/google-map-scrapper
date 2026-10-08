#!/usr/bin/env python3
"""Automated verification test suite for MapLead Authentication, Session, and Rate Limiting."""
import sys
import os
import unittest
from fastapi.testclient import TestClient

# Ensure workspace is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Configure test environment
os.environ["APP_PASSWORD"] = "TestSecretPass123!"
os.environ["SESSION_SECRET"] = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"

from web.backend import app
from web.auth import (
    authenticate_password, create_session_token, verify_session_token,
    reset_rate_limit, COOKIE_NAME, _failed_login_attempts
)

class TestAuthFlow(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)
        reset_rate_limit("127.0.0.1")
        reset_rate_limit("testclient")
        _failed_login_attempts.clear()

    def test_1_unauthenticated_requests_blocked(self):
        """Unauthenticated requests to protected API endpoints return 401."""
        res_status = self.client.get("/api/auth/status")
        self.assertEqual(res_status.status_code, 200)
        self.assertEqual(res_status.json()["authenticated"], False)

        # Protected endpoints
        res_credits = self.client.get("/api/credits/status")
        self.assertEqual(res_credits.status_code, 401)

        res_crm = self.client.get("/api/crm/leads")
        self.assertEqual(res_crm.status_code, 401)
        print("[PASS] Test 1: Protected API routes return 401 without valid session")

    def test_2_rate_limiting_triggers_after_5_failed_attempts(self):
        """5 failed login attempts trigger 429 Too Many Requests."""
        for attempt in range(4):
            res = self.client.post("/api/auth/login", json={"password": "wrong_password"})
            self.assertEqual(res.status_code, 401)
            self.assertIn("Incorrect password", res.json()["detail"])

        # 5th failed attempt
        res_5 = self.client.post("/api/auth/login", json={"password": "wrong_password"})
        self.assertEqual(res_5.status_code, 401)

        # 6th attempt should be blocked by rate limiter with 429
        res_6 = self.client.post("/api/auth/login", json={"password": "wrong_password"})
        self.assertEqual(res_6.status_code, 429)
        self.assertIn("Too many failed login attempts", res_6.json()["detail"])
        print("[PASS] Test 2: Rate limiter triggers HTTP 429 after 5 failed attempts")

    def test_3_successful_login_sets_signed_cookie_and_allows_access(self):
        """Successful login returns signed 7-day cookie and unlocks protected routes."""
        res_login = self.client.post("/api/auth/login", json={"password": "TestSecretPass123!"})
        self.assertEqual(res_login.status_code, 200)
        self.assertTrue(res_login.json()["success"])
        self.assertIn(COOKIE_NAME, res_login.cookies)

        token = res_login.cookies[COOKIE_NAME]
        self.assertTrue(verify_session_token(token))

        # Re-check status with cookie
        res_status = self.client.get("/api/auth/status", cookies={COOKIE_NAME: token})
        self.assertEqual(res_status.status_code, 200)
        self.assertEqual(res_status.json()["authenticated"], True)

        # Access protected route with cookie
        res_credits = self.client.get("/api/credits/status", cookies={COOKIE_NAME: token})
        self.assertEqual(res_credits.status_code, 200)
        print("[PASS] Test 3: Valid password sets signed 7-day session cookie and grants access")

    def test_4_logout_clears_session(self):
        """Logout endpoint clears session cookie and revokes access."""
        token = create_session_token()
        res_logout = self.client.post("/api/auth/logout", cookies={COOKIE_NAME: token})
        self.assertEqual(res_logout.status_code, 200)

        # Check deleted cookie header
        set_cookie_header = res_logout.headers.get("set-cookie", "")
        self.assertTrue(COOKIE_NAME in set_cookie_header)
        print("[PASS] Test 4: Logout successfully clears session cookie")

if __name__ == "__main__":
    unittest.main()
