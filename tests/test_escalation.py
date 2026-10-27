"""Evidence-driven two-failure upgrade, native max, pins and durable state."""
import copy
from pathlib import Path
import tempfile
import unittest

import escalation
import router


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.cfg = router.read_json(router.ROOT / "config.json")
        self.runtime = router.read_json(router.ROOT / "examples/runtime-demo.json")
        self.catalog = router.read_json(router.ROOT / "evidence/openrouter-text-free.json")
        self.task = router.normalize_task({"objective": "Review code and repair one small bug", "kind": "debugging", "complexity": 2})
        self.runtime["simulation"] = False
        for model in self.runtime["models"]:
            model["efforts"].append("max")

    def choices(self, task=None):
        return router.escalation_candidates(task or self.task, self.cfg, self.runtime, self.catalog)

    def test_two_failures_are_required_one_by_one_until_native_max_then_next_tier(self):
        route = {"model": "gpt-6.1-sol", "backend": "codex", "effort": "medium"}
        result, change = escalation.advance_route(self.task, route, self.choices())
        self.assertEqual((result["model"], result["effort"], change), ("gpt-6.1-sol", "high", "effort_up:medium->high"))
        result, change = escalation.advance_route(self.task, result, self.choices())
        self.assertEqual((result["model"], result["effort"]), ("gpt-6.1-sol", "xhigh"))
        result, change = escalation.advance_route(self.task, result, self.choices())
        self.assertEqual((result["model"], result["effort"]), ("gpt-6.1-sol", "max"))
        result, change = escalation.advance_route(self.task, result, self.choices())
        self.assertEqual((result["model"], result["effort"], change), ("gpt-6-astra", "medium", "tier_up:sol->astra"))

    def test_max_not_fabricated_when_runtime_only_supports_xhigh(self):
        for m in self.runtime["models"]:
            m["efforts"].remove("max")
        route = {"model": "gpt-6.1-sol", "backend": "codex", "effort": "xhigh"}
        result, reason = escalation.advance_route(self.task, route, self.choices())
        self.assertEqual(result["model"], "gpt-6-astra")
        self.assertEqual(reason, "tier_up:sol->astra")

    def test_explicit_model_pin_never_changes_family(self):
        task = router.normalize_task({**self.task, "requested_model": "gpt-6.1-sol"})
        route = {"model": "gpt-6.1-sol", "backend": "codex", "effort": "max"}
        self.assertEqual(escalation.advance_route(task, route, self.choices(task)),
                         (None, "requested_model_pin_prevents_tier_change"))

    def test_family_order_luna_terra_sol_astra(self):
        task = router.normalize_task({"objective": "basic change", "kind": "general", "complexity": 2})
        candidates = self.choices(task)
        route = {"model": "gpt-6-luna", "backend": "codex", "effort": "max"}
        steps = []
        for _ in range(3):
            route, reason = escalation.advance_route(task, route, candidates)
            steps.append((route["model"], reason))
            route["effort"] = "max"
        self.assertEqual(steps, [
            ("gpt-5.6-terra", "tier_up:luna->terra"),
            ("gpt-6.1-sol", "tier_up:terra->sol"),
            ("gpt-6-astra", "tier_up:sol->astra"),
        ])
        self.assertEqual(escalation.advance_route(task, route, candidates), (None, "highest_tier_exhausted"))

    def test_skips_missing_tier_and_does_not_downgrade(self):
        self.runtime["models"] = [m for m in self.runtime["models"] if m["id"] in ("gpt-6-luna", "gpt-6.1-sol")]
        task = router.normalize_task({"objective": "general", "kind": "general", "complexity": 1})
        res, reason = escalation.advance_route(task, {"model": "gpt-6-luna", "backend": "codex", "effort": "max"}, self.choices(task))
        self.assertEqual((res["model"], reason), ("gpt-6.1-sol", "tier_up:luna->sol"))
        res, reason = escalation.advance_route(task, {"model": "gpt-6.1-sol", "backend": "codex", "effort": "max"}, self.choices(task))
        self.assertIsNone(res)
        self.assertEqual(reason, "no_higher_eligible_tier")

    def test_free_text_without_native_effort_ascends_after_threshold(self):
        task = router.normalize_task(router.read_json(router.ROOT / "examples/task-public-summary.json"))
        choices = self.choices(task)
        self.assertTrue(any(c["family"] == "free" for c in choices))
        free = next(c for c in choices if c["family"] == "free")
        route = {"model": free["model_id"], "backend": "openrouter", "effort": None}
        res, reason = escalation.advance_route(task, route, choices)
        self.assertEqual((res["model"], res["backend"], reason), ("gpt-6-luna", "codex", "tier_up:free->luna"))


class PersistentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = escalation.RetryStore(self.root / "retry.sqlite")
        self.task = router.normalize_task({"objective": "Fix a Rust bug", "kind": "debugging", "complexity": 2})
        self.cfg = router.read_json(router.ROOT / "config.json")
        self.runtime = router.read_json(router.ROOT / "examples/runtime-demo.json")
        self.runtime["simulation"] = False
        for model in self.runtime["models"]:
            model["efforts"].append("max")
        self.catalog = router.read_json(router.ROOT / "evidence/openrouter-text-free.json")
        self.candidates = router.escalation_candidates(self.task, self.cfg, self.runtime, self.catalog)
        self.plan = {"status": "planned", "simulation": False, "selected_model": "gpt-6.1-sol", "backend": "codex", "requested_effort": "medium"}
        self.fingerprint = escalation.task_fingerprint(self.task, self.root)
        self.store.register("release-test", self.fingerprint, self.plan, self.candidates)

    def failure(self):
        attempt_id, _ = self.store.begin_attempt("release-test", self.fingerprint)
        return self.store.record_outcome("release-test", attempt_id, "failed", self.task,
                                         fingerprint=self.fingerprint, evidence_kind="verifier", evidence_sha256="a" * 64)

    def test_failure_one_does_nothing_two_upgrades_and_counter_resets(self):
        first = self.failure()
        self.assertEqual((first["failures"], first["route"]["effort"]), (1, "medium"))
        second = self.failure()
        self.assertEqual((second["failures"], second["route"]["effort"]), (0, "high"))
        self.assertEqual(second["total_failures"], 2)
        self.assertEqual(second["last_transition"], "effort_up:medium->high")
        third = self.failure()
        self.assertEqual((third["failures"], third["route"]["effort"]), (1, "high"))

    def test_a_completed_turn_without_check_never_increments_failures(self):
        attempt_id, _ = self.store.begin_attempt("release-test", self.fingerprint)
        after = self.store.record_outcome("release-test", attempt_id, "unverified", self.task,
                                          fingerprint=self.fingerprint, evidence_kind="worker")
        self.assertEqual(after["status"], "needs_verification")
        self.assertEqual(after["failures"], 0)
        with self.assertRaises(ValueError):
            self.store.begin_attempt("release-test", self.fingerprint)
        output = self.store.record_outcome("release-test", attempt_id, "failed", self.task,
                                            fingerprint=self.fingerprint, evidence_kind="manual",
                                            evidence_sha256="b" * 64, from_review=True)
        self.assertEqual((output["status"], output["failures"]), ("ready", 1))
        with self.assertRaisesRegex(ValueError, "finalized"):
            self.store.record_outcome("release-test", attempt_id, "failed", self.task,
                                      fingerprint=self.fingerprint, evidence_kind="manual",
                                      evidence_sha256="b" * 64, from_review=True)

    def test_explicit_success_stops_retry_and_not_just_model_claim(self):
        self.failure()
        attempt_id, _ = self.store.begin_attempt("release-test", self.fingerprint)
        after = self.store.record_outcome("release-test", attempt_id, "passed", self.task,
                                          fingerprint=self.fingerprint, evidence_kind="verifier", evidence_sha256="a" * 64)
        self.assertEqual((after["status"], after["failures"]), ("completed", 0))
        with self.assertRaises(ValueError):
            self.store.begin_attempt("release-test", self.fingerprint)

    def test_environment_or_timeout_unknown_never_upgrades(self):
        self.failure()
        attempt_id, _ = self.store.begin_attempt("release-test", self.fingerprint)
        after = self.store.record_outcome("release-test", attempt_id, "blocked", self.task,
                                          fingerprint=self.fingerprint, evidence_kind="worker")
        self.assertEqual((after["status"], after["failures"], after["route"]["effort"]), ("paused", 1, "medium"))
        with self.assertRaises(ValueError):
            self.store.begin_attempt("release-test", self.fingerprint)

    def test_fingerprint_and_attempt_cannot_cross_jobs(self):
        with self.assertRaises(ValueError):
            self.store.begin_attempt("release-test", "c" * 64)
        with self.assertRaises(ValueError):
            self.store.register("release-test", "f" * 64, self.plan, self.candidates)
        attempt, _ = self.store.begin_attempt("release-test", self.fingerprint)
        with self.assertRaises(ValueError):
            self.store.record_outcome("release-test", "no-such-attempt", "failed", self.task,
                                      fingerprint=self.fingerprint, evidence_kind="verifier")
        self.assertEqual(self.store.get("release-test")["status"], "running")

    def test_requested_pin_exhausts_at_max(self):
        task = router.normalize_task({**self.task, "requested_model": "gpt-6.1-sol"})
        fingerprint = escalation.task_fingerprint(task, self.root)
        choices = router.escalation_candidates(task, self.cfg, self.runtime, self.catalog)
        plan = dict(self.plan, requested_effort="max")
        self.store.register("pinned", fingerprint, plan, choices)
        for i in range(2):
            attempt, _ = self.store.begin_attempt("pinned", fingerprint)
            result = self.store.record_outcome("pinned", attempt, "failed", task, fingerprint=fingerprint, evidence_kind="verifier")
        self.assertEqual((result["status"], result["route"]["model"], result["last_transition"]),
                         ("exhausted", "gpt-6.1-sol", "requested_model_pin_prevents_tier_change"))


if __name__ == "__main__":
    unittest.main()
