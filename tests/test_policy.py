"""Local hard-gate tests. The free catalog filter is tested by its own backend."""

import copy
import sys
import types
import unittest
from unittest.mock import patch

from policy import build_candidates, enforce_selection, fallback_route, normalize_task, routing_state


PROFILES = [
    {"id": "gpt-6-luna", "family": "luna", "description": "Current light model", "priority": 10, "legacy": False},
    {"id": "gpt-5.6-luna", "family": "luna", "description": "Legacy light model", "priority": 20, "legacy": True},
    {"id": "gpt-5.6-terra", "family": "terra", "description": "Legacy general model", "priority": 30, "legacy": True},
    {"id": "gpt-6.1-sol", "family": "sol", "description": "Current implementation", "priority": 40, "legacy": False},
    {"id": "gpt-6-sol", "family": "sol", "description": "Implementation", "priority": 50, "legacy": False},
    {"id": "gpt-5.6-sol", "family": "sol", "description": "Legacy implementation", "priority": 60, "legacy": True},
    {"id": "gpt-6-astra", "family": "astra", "description": "Complex reasoning", "priority": 70, "legacy": False},
]
FREE_ID = "example/text:free"
FREE_RECORD = {"id": FREE_ID, "context_length": 10000, "pricing": {"prompt": "0", "completion": "0"}}


def config():
    return {
        "gpt_profiles": copy.deepcopy(PROFILES),
        "free_profiles": [{"id": FREE_ID, "description": "Light text work", "priority": 5, "kinds": ["summary", "translation"], "max_complexity": 2, "enabled": True}],
        "policy": {"max_effort": "xhigh", "allow_legacy_auto": False},
    }


def runtime(ids=None, efforts=None):
    if ids is None:
        ids = [profile["id"] for profile in PROFILES]
    return {"models": [{"id": identifier, "efforts": efforts or ["none", "low", "medium", "high", "xhigh", "max"], "default_effort": "medium"} for identifier in ids], "simulation": True}


def task(**overrides):
    return normalize_task({"objective": "Complete this task.", **overrides})


def candidates(work=None, cfg=None, run=None):
    return build_candidates(work or task(), cfg or config(), run or runtime(), {"data": [copy.deepcopy(FREE_RECORD)]})


def ids(values):
    return {value["id"] for value in values}


class NormalizeTests(unittest.TestCase):
    def test_defaults_and_allowlist(self):
        value = normalize_task({"objective": "Keep exact text. ", "source": "secret", "api_key": "secret"})
        self.assertEqual(value["objective"], "Keep exact text. ")
        self.assertEqual(value["complexity"], 2)
        self.assertEqual(value["risk"], "medium")
        self.assertEqual(value["data_policy"], "metadata_only")
        self.assertFalse(value["summary_approved"])
        self.assertNotIn("api_key", value)
        self.assertNotIn("source", value)

    def test_tool_defaults(self):
        for kind in ("coding", "debugging", "architecture"):
            self.assertTrue(task(kind=kind)["needs_tools"])
        self.assertFalse(task(kind="coding", needs_tools=False)["needs_tools"])

    def test_invalid_schema_values(self):
        for value in ({"objective": ""}, {"complexity": True}, {"complexity": 5}, {"input_tokens": -1},
                      {"output_tokens": 8193}, {"kind": "unknown"}, {"risk": "critical"},
                      {"needs_tools": 1}, {"summary_approved": "yes"}, {"requested_model": ""},
                      {"implementation_failures": 6}, {"failure_kind": "network"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                task(**value)


class CandidateTests(unittest.TestCase):
    def test_runtime_is_required_for_gpt(self):
        self.assertEqual(build_candidates(task(), config(), {"models": []}, {"data": []}), [])
        self.assertEqual(ids(candidates(run=runtime(["gpt-6-sol"]))), {"gpt-6-sol"})

    def test_no_synthetic_or_unknown_model_ids(self):
        self.assertEqual(candidates(run=runtime(["gpt-6-terra", "gpt-6.1-astra"])), [])
        self.assertEqual(candidates(task(requested_model="gpt-6.1-astra")), [])

    def test_high_risk_and_extreme_require_astra(self):
        for work in (task(risk="high"), task(complexity=4)):
            with self.subTest(work=work):
                self.assertEqual(ids(candidates(work)), {"gpt-6-astra"})
                self.assertEqual(candidates(work, run=runtime(["gpt-6.1-sol"])), [])

    def test_complex_requires_sol_or_astra(self):
        self.assertEqual({candidate["family"] for candidate in candidates(task(complexity=3))}, {"sol", "astra"})
        self.assertEqual(candidates(task(complexity=3), run=runtime(["gpt-6-luna", "gpt-5.6-terra"])), [])

    def test_exact_pin_preserved_and_not_relaxed_for_high_risk(self):
        self.assertEqual(ids(candidates(task(requested_model="gpt-6-sol"))), {"gpt-6-sol"})
        self.assertEqual(candidates(task(requested_model="gpt-6-sol", risk="high")), [])
        with self.assertRaises(ValueError):
            fallback_route(task(requested_model="missing"), [])

    def test_legacy_is_only_a_fallback_by_default(self):
        current = ids(candidates())
        self.assertNotIn("gpt-5.6-luna", current)
        self.assertNotIn("gpt-5.6-sol", current)
        self.assertNotIn("gpt-5.6-terra", current)
        self.assertEqual(ids(candidates(run=runtime(["gpt-5.6-luna", "gpt-5.6-sol"]))), {"gpt-5.6-luna", "gpt-5.6-sol"})
        self.assertEqual(ids(candidates(run=runtime(["gpt-5.6-terra"]))), {"gpt-5.6-terra"})

    def test_explicit_legacy_and_allow_legacy_auto(self):
        for field in ("requested_model", "compatibility_model"):
            self.assertIn("gpt-5.6-terra", ids(candidates(task(**{field: "gpt-5.6-terra"}))))
        cfg = config()
        cfg["policy"]["allow_legacy_auto"] = True
        self.assertEqual(ids(candidates(cfg=cfg)), {profile["id"] for profile in PROFILES})

    def test_ineligible_modern_does_not_suppress_usable_legacy(self):
        run = runtime(["gpt-6-luna", "gpt-5.6-luna"])
        run["models"][0]["efforts"] = ["low"]
        self.assertEqual(ids(candidates(run=run)), {"gpt-5.6-luna"})

    def test_native_efforts_are_intersected_and_capped(self):
        cfg = config()
        cfg["policy"]["max_effort"] = "high"
        value = candidates(cfg=cfg, run=runtime(["gpt-6-sol"], ["none", "medium", "high", "xhigh", "max", "ultra"]))[0]
        self.assertEqual(value["efforts"], ["none", "medium", "high"])
        self.assertEqual(value["default_effort"], "medium")
        self.assertEqual(candidates(task(complexity=4), cfg=cfg), [])

    def test_floor_escalation_only_for_implementation_failure(self):
        run = runtime(["gpt-6-sol"], ["low", "medium"])
        for kind in ("none", "environment", "quota", "permission"):
            with self.subTest(kind=kind):
                self.assertEqual(ids(candidates(task(implementation_failures=3, failure_kind=kind), run=run)), {"gpt-6-sol"})
        self.assertEqual(ids(candidates(task(implementation_failures=1, failure_kind="implementation"), run=run)), {"gpt-6-sol"})
        work = task(complexity=1, implementation_failures=5, failure_kind="implementation")
        self.assertEqual(fallback_route(work, candidates(work))["effort"], "low")


class FreeCandidateTests(unittest.TestCase):
    def setUp(self):
        # A contract fixture, not a second implementation of free-price checking.
        self.backend = types.ModuleType("openrouter_backend")
        self.backend.get_free_models = lambda catalog, modality="text": catalog["data"]
        self.patcher = patch.dict(sys.modules, {"openrouter_backend": self.backend})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.work = task(kind="summary", risk="low", data_policy="public", needs_tools=False)

    def test_free_eligible_and_fallback_preference(self):
        choices = candidates(self.work)
        self.assertIn(FREE_ID, ids(choices))
        chosen = fallback_route(self.work, choices)
        self.assertEqual(chosen["selected_id"], FREE_ID)
        self.assertEqual(chosen["effort"], "low")
        free = next(candidate for candidate in choices if candidate["id"] == FREE_ID)
        self.assertEqual(free["efforts"], ["low"])
        self.assertIn("no native reasoning-effort", free["description"])

    def test_free_hard_gates(self):
        for changes in ({"risk": "medium"}, {"complexity": 3}, {"needs_tools": True},
                        {"data_policy": "metadata_only"}, {"data_policy": "no_external"},
                        {"kind": "research"}):
            work = {**self.work, **changes}
            with self.subTest(changes=changes):
                self.assertNotIn(FREE_ID, ids(candidates(work)))

    def test_profile_gate_and_missing_free_catalog(self):
        for change in ({"enabled": False}, {"max_complexity": 1}, {"kinds": ["translation"]}):
            cfg = config()
            cfg["free_profiles"][0].update(change)
            with self.subTest(change=change):
                self.assertNotIn(FREE_ID, ids(candidates(self.work, cfg=cfg)))
        self.assertNotIn(FREE_ID, ids(build_candidates(self.work, config(), runtime(), {"data": []})))

    def test_free_context_reserve(self):
        self.assertIn(FREE_ID, ids(candidates({**self.work, "input_tokens": 1856})))
        self.assertNotIn(FREE_ID, ids(candidates({**self.work, "input_tokens": 1857})))

    def test_actual_objective_bounds_input_when_estimate_is_absent(self):
        work = task(objective="中" * 620, kind="summary", risk="low", data_policy="public")
        self.assertEqual(work["input_tokens"], 0)
        self.assertNotIn(FREE_ID, ids(candidates(work)))
        short = {**self.work, "input_tokens": 1857}
        self.assertNotIn(FREE_ID, ids(candidates(short)))

    def test_provider_output_limit_is_applied_before_selection(self):
        record = {**copy.deepcopy(FREE_RECORD), "top_provider": {"max_completion_tokens": 1024}}
        choices = build_candidates(self.work, config(), runtime(), {"data": [record]})
        self.assertNotIn(FREE_ID, ids(choices))
        record["top_provider"]["max_completion_tokens"] = 2048
        self.assertIn(FREE_ID, ids(build_candidates(self.work, config(), runtime(), {"data": [record]})))

    def test_text_worker_rejects_visual_qa_even_if_profile_lists_it(self):
        cfg = config()
        cfg["free_profiles"][0]["kinds"].append("visual_qa")
        self.assertNotIn(FREE_ID, ids(candidates({**self.work, "kind": "visual_qa"}, cfg=cfg)))

    def test_pin_does_not_bypass_free_policy(self):
        self.assertEqual(candidates(task(requested_model=FREE_ID, kind="summary", risk="medium", data_policy="public")), [])
        self.assertEqual(candidates(task(requested_model=FREE_ID, kind="summary", risk="low", data_policy="metadata_only")), [])

    def test_stale_free_list_cannot_bypass_profile_or_context_gates(self):
        choices = candidates(self.work)
        for changes in ({"kind": "research"}, {"input_tokens": 1857}, {"objective": "中" * 620}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                enforce_selection({**self.work, **changes}, choices, FREE_ID, "low")


class SelectionTests(unittest.TestCase):
    def test_family_preferences_and_priority(self):
        self.assertEqual(fallback_route(task(), candidates())["selected_id"], "gpt-6-luna")
        for kind in ("coding", "debugging", "architecture", "research"):
            work = task(kind=kind)
            self.assertEqual(fallback_route(work, candidates(work))["selected_id"], "gpt-6.1-sol")
        work = task(risk="high")
        self.assertEqual(fallback_route(work, candidates(work))["selected_id"], "gpt-6-astra")

    def test_compatibility_preference(self):
        work = task(compatibility_model="gpt-5.6-terra")
        self.assertEqual(fallback_route(work, candidates(work))["selected_id"], "gpt-5.6-terra")

    def test_no_pin_substitution_even_with_supplied_candidate_list(self):
        work = task(requested_model="gpt-5.6-sol")
        with self.assertRaises(ValueError):
            enforce_selection(work, candidates(), "gpt-6-sol", "medium")
        with self.assertRaises(ValueError):
            fallback_route(work, candidates())

    def test_effort_floor_and_cap_enforced(self):
        work = task(complexity=3)
        cfg = config()
        cfg["policy"]["max_effort"] = "high"
        choices = candidates(work, cfg=cfg)
        self.assertEqual(enforce_selection(work, choices, "gpt-6-sol", "low")["effort"], "high")
        self.assertEqual(enforce_selection(work, choices, "gpt-6-sol", "xhigh")["effort"], "high")
        with self.assertRaises(ValueError):
            enforce_selection(work, choices, "unknown", "high")
        self.assertEqual(enforce_selection(work, choices, "gpt-6-sol", "max")["effort"], "high")

    def test_stale_list_cannot_lower_family_floor(self):
        choices = candidates(run=runtime(["gpt-6-sol"]))
        with self.assertRaises(ValueError):
            fallback_route(task(risk="high"), choices)


class PrivacyTests(unittest.TestCase):
    def test_defaults_and_no_external_emit_nothing(self):
        self.assertIsNone(routing_state(task(objective="private full content")))
        self.assertIsNone(routing_state(task(data_policy="no_external", summary_approved=True, summary="approved")))

    def test_metadata_only_never_sends_objective(self):
        value = routing_state({"objective": "PRIVATE-FULL-TEXT", "summary": "Approved bounded bug summary", "summary_approved": True, "source_file": "PRIVATE-PATH", "credentials": "PRIVATE-KEY"})
        self.assertEqual(value["summary"], "Approved bounded bug summary")
        self.assertNotIn("objective", value)
        self.assertNotIn("PRIVATE", str(value))

    def test_approval_and_nonempty_summary_both_required(self):
        self.assertIsNone(routing_state(task(summary="summary", summary_approved=False)))
        self.assertIsNone(routing_state(task(summary=" ", summary_approved=True)))

    def test_public_uses_objective(self):
        value = routing_state(task(objective="public objective", summary="different", data_policy="public"))
        self.assertEqual(value["summary"], "public objective")

    def test_oversize_is_rejected_not_truncated(self):
        with self.assertRaisesRegex(ValueError, "exceeds"):
            routing_state(task(objective="中" * 10000, data_policy="public"))


if __name__ == "__main__":
    unittest.main()
