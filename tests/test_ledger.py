from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from decimal import Decimal
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ledger import BudgetExceeded, Ledger


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "budget.db"
        self.policy = {
            "requests_per_minute": 100, "requests_per_job": 2,
            "free_requests_per_utc_day": 40, "decision_requests_per_utc_day": 1000,
            "decision_cache_seconds": 10,
        }
        self.paid = {"max_request_cost_usd": "0.01", "max_daily_cost_usd": "0.10"}
        self.db = Ledger(self.path, self.policy)
        self.now = 100000

    def request_count(self):
        with self.db.connect() as db:
            return db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]

    def summary(self, now=None):
        return self.db.paid_daily_summary(self.now if now is None else now)

    def reserve_paid(self, job="a", policy=None, now=None):
        return self.db.reserve_decision(job, self.paid if policy is None else policy, self.now if now is None else now)

    def test_minute_budget_counts_both_categories(self):
        self.db = Ledger(self.path, {**self.policy, "requests_per_minute": 2})
        self.reserve_paid("a")
        self.assertIsNone(self.db.reserve("b", "free_worker", self.now + 1))
        with self.assertRaisesRegex(BudgetExceeded, "requests_per_minute"):
            self.db.reserve("c", "free_worker", self.now + 2)
        self.db.reserve("c", "free_worker", self.now + 62)

    def test_free_and_decision_daily_counts_are_independent(self):
        self.db = Ledger(self.path, {**self.policy, "free_requests_per_utc_day": 1, "decision_requests_per_utc_day": 1})
        self.db.reserve("free", "free_worker", self.now)
        self.reserve_paid("paid")
        with self.assertRaisesRegex(BudgetExceeded, "free_requests_per_utc_day"):
            self.db.reserve("another-free", "free_worker", self.now + 100)
        with self.assertRaisesRegex(BudgetExceeded, "decision_requests_per_utc_day"):
            self.reserve_paid("another-paid", now=self.now + 100)
        self.assertEqual(self.request_count(), 2)

    def test_legacy_daily_setting_falls_back_separately(self):
        self.db = Ledger(self.path, {
            "requests_per_minute": 100, "requests_per_utc_day": 2,
            "requests_per_job": 2, "decision_cache_seconds": 10,
        })
        for i in range(2):
            self.db.reserve(f"free-{i}", "free_worker", self.now)
            self.db.reserve(f"legacy-decision-{i}", "decision", self.now)
        for kind in ("free_worker", "decision"):
            with self.subTest(kind=kind), self.assertRaises(BudgetExceeded):
                self.db.reserve("one-more", kind, self.now)
        self.assertEqual(self.request_count(), 4)
        self.assertEqual(self.summary()["charged_or_reserved_usd"], "0.000000000")

    def test_daily_request_counts_reset_at_utc_boundary(self):
        self.db = Ledger(self.path, {**self.policy, "free_requests_per_utc_day": 1})
        self.db.reserve("today", "free_worker", self.now)
        with self.assertRaises(BudgetExceeded):
            self.db.reserve("still-today", "free_worker", 172799)
        self.db.reserve("tomorrow", "free_worker", 172800)

    def test_job_budget_combines_paid_decisions_and_free_workers(self):
        self.reserve_paid("a")
        self.db.reserve("a", "free_worker", self.now + 100)
        with self.assertRaisesRegex(BudgetExceeded, "requests_per_job"):
            self.reserve_paid("a", now=self.now + 200)
        self.assertEqual(self.summary()["reservation_count"], 1)
        self.assertEqual(self.request_count(), 2)

    def test_new_unknown_request_kinds_are_rejected(self):
        for kind in ("worker", "paid", "", None, {}):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.db.reserve("a", kind, self.now)
        self.assertEqual(self.request_count(), 0)

    def test_full_cap_is_reserved_not_estimated_cost(self):
        policy = {**self.paid, "max_daily_cost_usd": "0.015", "estimated_cost_usd": "0.0001"}
        identifier = self.reserve_paid(policy=policy)
        self.assertIsInstance(identifier, int)
        self.assertGreater(identifier, 0)
        self.assertEqual(self.summary()["reserved_usd"], "0.010000000")
        with self.assertRaisesRegex(BudgetExceeded, "max_daily_cost_usd"):
            self.reserve_paid("b", policy)
        self.assertEqual(self.summary()["reservation_count"], 1)
        self.assertEqual(self.request_count(), 1)

    def test_known_cost_releases_only_unused_reservation(self):
        policy = {**self.paid, "max_daily_cost_usd": "0.015"}
        identifier = self.reserve_paid(policy=policy)
        settled = self.db.settle_decision(identifier, "0.002", self.now + 1)
        self.assertEqual(settled["actual_cost_usd"], "0.002000000")
        self.assertEqual(settled["reserved_usd"], "0.000000000")
        self.assertEqual(settled["released_usd"], "0.008000000")
        self.reserve_paid("b", policy)
        summary = self.summary()
        self.assertEqual(summary["reserved_usd"], "0.010000000")
        self.assertEqual(summary["settled_usd"], "0.002000000")
        self.assertEqual(summary["charged_or_reserved_usd"], "0.012000000")
        self.assertEqual(summary["settled_count"], 1)

    def test_no_response_does_not_release_reservation(self):
        policy = {**self.paid, "max_daily_cost_usd": "0.01"}
        self.reserve_paid(policy=policy)
        with self.assertRaises(BudgetExceeded):
            self.reserve_paid("b", policy)
        self.assertEqual(self.summary()["reserved_usd"], "0.010000000")
        self.assertEqual(self.summary()["unsettled_count"], 1)

    def test_unknown_cost_retains_full_reservation_and_can_be_resolved(self):
        policy = {**self.paid, "max_daily_cost_usd": "0.01"}
        identifier = self.reserve_paid(policy=policy)
        unknown = self.db.settle_decision(identifier, None, self.now + 1)
        self.assertEqual(unknown["status"], "unknown")
        self.assertIsNone(unknown["actual_cost_usd"])
        self.assertEqual(unknown["charged_or_reserved_usd"], "0.010000000")
        self.assertEqual(self.summary()["unknown_count"], 1)
        self.assertEqual(unknown, self.db.settle_decision(identifier, None, self.now + 2))
        with self.assertRaises(BudgetExceeded):
            self.reserve_paid("b", policy)
        self.db.settle_decision(identifier, "0.0002", self.now + 3)
        self.assertEqual(self.summary()["unknown_count"], 0)
        self.assertEqual(self.summary()["settled_usd"], "0.000200000")

    def test_zero_known_cost_is_distinct_from_unknown(self):
        identifier = self.reserve_paid()
        result = self.db.settle_decision(identifier, 0, self.now)
        self.assertEqual(result["status"], "settled")
        self.assertEqual(result["actual_cost_usd"], "0.000000000")
        self.assertEqual(result["released_usd"], "0.010000000")
        self.assertEqual(self.summary()["unsettled_count"], 0)

    def test_repeated_known_settlement_is_idempotent(self):
        identifier = self.reserve_paid()
        first = self.db.settle_decision(identifier, "0.002000000", self.now)
        second = self.db.settle_decision(identifier, Decimal("0.002"), self.now + 1)
        third = self.db.settle_decision(identifier, 0.002, self.now + 2)
        self.assertEqual(first, second)
        self.assertEqual(first, third)
        self.assertEqual(self.summary()["settled_usd"], "0.002000000")

    def test_conflicting_settlement_does_not_change_known_cost(self):
        identifier = self.reserve_paid()
        self.db.settle_decision(identifier, "0.002", self.now)
        for changed in ("0.003", None):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "conflict"):
                self.db.settle_decision(identifier, changed, self.now + 1)
        self.assertEqual(self.summary()["settled_usd"], "0.002000000")

    def test_overrun_is_committed_before_raise_and_blocks_paid_for_day(self):
        identifier = self.reserve_paid()
        with self.assertRaisesRegex(BudgetExceeded, "decision_cost_overrun"):
            self.db.settle_decision(identifier, "0.010000001", self.now)
        snapshot = self.summary()
        self.assertTrue(snapshot["cost_overrun"])
        self.assertEqual(snapshot["reserved_usd"], "0.000000000")
        self.assertEqual(snapshot["settled_usd"], "0.010000001")
        with self.assertRaisesRegex(BudgetExceeded, "decision_cost_overrun"):
            self.db.settle_decision(identifier, "0.010000001", self.now + 1)
        self.assertEqual(snapshot, self.summary())
        with self.assertRaisesRegex(BudgetExceeded, "decision_cost_overrun"):
            self.reserve_paid("b", {"max_request_cost_usd": "1", "max_daily_cost_usd": "100"})
        self.assertEqual(self.request_count(), 1)
        self.db.reserve("free-after-overrun", "free_worker", self.now)

    def test_daily_reservation_and_overrun_reset_at_utc_boundary(self):
        identifier = self.reserve_paid(policy={**self.paid, "max_daily_cost_usd": "0.01"})
        self.assertEqual(self.summary(172800)["charged_or_reserved_usd"], "0.000000000")
        self.reserve_paid("next-day", now=172800)
        with self.assertRaises(BudgetExceeded):
            self.db.settle_decision(identifier, "0.02", 172801)
        self.assertTrue(self.summary(self.now)["cost_overrun"])
        self.assertFalse(self.summary(172801)["cost_overrun"])
        self.reserve_paid("next-day-2", now=172802)

    def test_invalid_actual_cost_never_releases_reservation(self):
        identifier = self.reserve_paid()
        for bad in (True, False, float("nan"), float("inf"), "NaN", "sNaN", "Infinity", "-0.01", "0.0000000001", {}, "9223372036.854775808"):
            with self.subTest(bad=repr(bad)), self.assertRaises(ValueError):
                self.db.settle_decision(identifier, bad, self.now)
            self.assertEqual(self.summary()["reserved_usd"], "0.010000000")
            self.assertEqual(self.summary()["settled_usd"], "0.000000000")

    def test_invalid_spending_caps_create_no_request_or_reservation(self):
        for field in ("max_request_cost_usd", "max_daily_cost_usd"):
            for bad in (True, False, "NaN", float("inf"), "-1", "0.0000000001", None):
                with self.subTest(field=field, bad=repr(bad)), self.assertRaises(ValueError):
                    self.reserve_paid(policy={**self.paid, field: bad})
        self.assertEqual(self.request_count(), 0)
        self.assertEqual(self.summary()["reservation_count"], 0)

    def test_zero_daily_allowance_blocks_before_request(self):
        with self.assertRaises(BudgetExceeded):
            self.reserve_paid(policy={**self.paid, "max_daily_cost_usd": "0"})
        self.assertEqual(self.request_count(), 0)

    def test_cost_is_stored_as_integer_nanousd(self):
        identifier = self.reserve_paid(policy={"max_request_cost_usd": "0.000000003", "max_daily_cost_usd": "0.000000003"})
        self.db.settle_decision(identifier, "0.000000001", self.now)
        with self.db.connect() as db:
            row = db.execute("SELECT reserved_nano, actual_nano, typeof(reserved_nano), typeof(actual_nano) FROM paid_decisions").fetchone()
        self.assertEqual(row, (3, 1, "integer", "integer"))
        self.assertEqual(self.summary()["settled_usd"], "0.000000001")

    def test_unknown_reservation_and_boolean_id_are_rejected(self):
        for identifier in (9999, True, False, 0, -1, "1"):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.db.settle_decision(identifier, "0.001", self.now)
        self.assertEqual(self.summary()["reservation_count"], 0)

    def test_invalid_counter_and_timestamp_values_are_rejected(self):
        for setting in ("requests_per_minute", "requests_per_job", "free_requests_per_utc_day", "decision_requests_per_utc_day"):
            for value in (True, 1.5, "2", -1):
                with self.subTest(setting=setting, value=value), self.assertRaises(ValueError):
                    Ledger(self.path, {**self.policy, setting: value})
        for bad in (True, float("nan"), float("inf"), -1):
            with self.subTest(now=repr(bad)), self.assertRaises(ValueError):
                self.reserve_paid(now=bad)

    def test_request_count_failure_rolls_back_paid_reservation(self):
        self.db = Ledger(self.path, {**self.policy, "decision_requests_per_utc_day": 0})
        with self.assertRaises(BudgetExceeded):
            self.reserve_paid()
        self.assertEqual(self.summary()["reservation_count"], 0)
        self.assertEqual(self.request_count(), 0)

    def test_database_write_failure_rolls_back_both_reservation_and_count(self):
        with patch.object(self.db, "_record_request", side_effect=sqlite3.OperationalError("write failed")):
            with self.assertRaises(sqlite3.OperationalError):
                self.reserve_paid()
        self.assertEqual(self.summary()["reservation_count"], 0)
        self.assertEqual(self.request_count(), 0)

    def test_concurrent_instances_cannot_oversubscribe_daily_money(self):
        policy = {**self.paid, "max_daily_cost_usd": "0.03"}
        def reserve(index):
            instance = Ledger(self.path, self.policy)
            try:
                return instance.reserve_decision(f"job-{index}", policy, self.now)
            except BudgetExceeded:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(reserve, range(16)))
        self.assertEqual(len([identifier for identifier in ids if identifier is not None]), 3)
        self.assertEqual(self.request_count(), 3)
        self.assertEqual(self.summary()["charged_or_reserved_usd"], "0.030000000")

    def test_concurrent_free_and_paid_share_overall_request_cap(self):
        self.policy["requests_per_minute"] = 3
        self.db = Ledger(self.path, self.policy)
        def reserve(index):
            instance = Ledger(self.path, self.policy)
            try:
                if index % 2:
                    instance.reserve_decision(f"job-{index}", self.paid, self.now)
                else:
                    instance.reserve(f"job-{index}", "free_worker", self.now)
                return True
            except BudgetExceeded:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(reserve, range(16)))
        self.assertEqual(sum(outcomes), 3)
        self.assertEqual(self.request_count(), 3)

    def test_existing_cache_and_request_tables_are_preserved(self):
        old_path = Path(self.tmp.name) / "old.db"
        with closing(sqlite3.connect(old_path)) as old, old:
            old.execute("CREATE TABLE requests (ts REAL, job TEXT, kind TEXT)")
            old.execute("CREATE TABLE decisions (key TEXT PRIMARY KEY, ts REAL, body TEXT)")
            old.execute("INSERT INTO requests VALUES (?, 'old', 'worker')", (self.now,))
            old.execute("INSERT INTO decisions VALUES ('cached', ?, ?)", (self.now, json.dumps({"answer": "kept"})))
        migrated = Ledger(old_path, {**self.policy, "free_requests_per_utc_day": 1})
        self.assertEqual(migrated.get("cached", self.now + 1), {"answer": "kept"})
        with self.assertRaises(BudgetExceeded):
            migrated.reserve("new-free", "free_worker", self.now)
        migrated.reserve_decision("new-paid", self.paid, self.now)
        with migrated.connect() as db:
            self.assertEqual(db.execute("SELECT kind FROM requests WHERE job = 'old'").fetchone()[0], "worker")

    def test_cache_expiry_and_clock_regression_remain_unchanged(self):
        self.db.put("k", {"probability": 0.96}, self.now)
        self.assertEqual(self.db.get("k", self.now + 5), {"probability": 0.96})
        self.assertIsNone(self.db.get("k", self.now + 11))
        self.assertIsNone(self.db.get("k", self.now - 1))


if __name__ == "__main__":
    unittest.main()
