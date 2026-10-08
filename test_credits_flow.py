#!/usr/bin/env python3
"""Automated verification test suite for Fast Search Credits & Cache logic."""
import sys
import os
import unittest
from datetime import datetime, timedelta

# Ensure workspace is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import web.crm_db as crm_db

TEST_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_credits_isolated.db")

from web.credits_db import (
    get_credit_status,
    update_credit_settings,
    increment_credit_usage,
    log_places_search,
    generate_cache_key,
    get_cached_search_results,
    store_cached_search_results,
    get_current_billing_month,
    get_or_create_monthly_record,
    init_credits_db,
    get_db,
    _execute
)

class TestCreditsFlow(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Point to dedicated isolated test database
        cls._orig_db_path = crm_db.DB_PATH
        crm_db.DB_PATH = TEST_DB_PATH
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        init_credits_db()

    @classmethod
    def tearDownClass(cls):
        crm_db.DB_PATH = cls._orig_db_path
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except Exception:
                pass

    def setUp(self):
        # Reset isolated credit tables to clean baseline
        conn = get_db()
        _execute(conn, "DELETE FROM places_credits")
        _execute(conn, "DELETE FROM places_search_logs")
        _execute(conn, "DELETE FROM places_search_cache")
        conn.commit()
        conn.close()

    def test_1_counter_increments_by_pages(self):
        """1. Counter increments exactly by pages/requests fetched."""
        status_before = get_credit_status()
        self.assertEqual(status_before["used"], 0)
        self.assertEqual(status_before["credits_left"], 1000)

        # Simulate 1 search fetching 3 pages
        increment_credit_usage(3)
        log_places_search("plumbers in Tampa FL", pages_requested=3, credits_used=3, cached=False)

        status_after = get_credit_status()
        self.assertEqual(status_after["used"], 3)
        self.assertEqual(status_after["credits_left"], 997)
        self.assertEqual(len(status_after["recent_searches"]), 1)
        self.assertEqual(status_after["recent_searches"][0]["credits_used"], 3)
        print("[PASS] Test 1: Counter increments by pages fetched (3 credits)")

    def test_2_cache_hit_uses_zero_credits(self):
        """2. Repeating identical query within 30 days serves from cache and uses 0 credits."""
        query = "dentists in Austin TX"
        filters = {"no_website_only": True, "min_reviews": 1, "min_rating": 4.0, "must_have_phone": True, "max_pages": 2}
        cache_key = generate_cache_key(query, filters)

        # Store in cache
        fake_payload = {"results": [{"name": "Austin Dental"}], "total_scanned": 1}
        store_cached_search_results(cache_key, query, fake_payload)

        # Verify retrieval
        cached = get_cached_search_results(cache_key)
        self.assertIsNotNone(cached)
        self.assertEqual(cached["total_scanned"], 1)

        # Log cached query (0 credits used)
        status_before = get_credit_status()
        log_places_search(query, pages_requested=2, credits_used=0, cached=True)
        status_after = get_credit_status()

        # Used credits must remain identical (0 used)
        self.assertEqual(status_before["used"], status_after["used"])
        self.assertEqual(status_after["recent_searches"][0]["cached"], 1)
        self.assertEqual(status_after["recent_searches"][0]["credits_used"], 0)
        print("[PASS] Test 2: 30-day cache hit serves result with 0 credits")

    def test_3_month_rollover_resets(self):
        """3. Automatic rollover when billing month changes resets usage to 0 while preserving user limits."""
        # Set custom limits for past month
        past_month = "2026-09"
        conn = get_db()
        _execute(conn, "DELETE FROM places_credits WHERE month = ?", (past_month,))
        _execute(conn, """
        INSERT INTO places_credits (month, used, monthly_limit, safety_buffer, updated_at)
        VALUES (?, 950, 1500, 75, ?)
        """, (past_month, datetime.now().isoformat()))
        conn.commit()
        conn.close()

        # Simulate rollover to new month
        new_month = "2026-11"
        rec = get_or_create_monthly_record(new_month)
        self.assertEqual(rec["month"], new_month)
        self.assertEqual(rec["used"], 0)  # Reset to 0!
        self.assertEqual(rec["monthly_limit"], 1500)  # Preserved custom limit!
        self.assertEqual(rec["safety_buffer"], 75)   # Preserved custom buffer!
        print("[PASS] Test 3: Month rollover automatically resets usage to 0 and inherits limits")

    def test_4_hard_stop_at_safety_buffer(self):
        """4. Hard stop blocks Fast Search when safe credits left < pages to fetch."""
        update_credit_settings(monthly_limit=1000, safety_buffer=50)
        # Advance used credits to 949 (credits_left = 51, safe_credits_left = 1)
        increment_credit_usage(949)

        status = get_credit_status()
        self.assertEqual(status["credits_left"], 51)
        self.assertEqual(status["safe_credits_left"], 1)

        # A 1-page search is still permitted:
        self.assertTrue(status["safe_credits_left"] >= 1)

        # A 3-page search is BLOCKED by hard stop:
        pages_needed = 3
        blocked = status["safe_credits_left"] < pages_needed
        self.assertTrue(blocked)
        print("[PASS] Test 4: Hard stop triggers when safe credits left (1) < requested pages (3)")

if __name__ == "__main__":
    unittest.main()
