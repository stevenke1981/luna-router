"""Offline contract and malformed-response tests; no inference calls."""

import copy
import json
import unittest

from decision import (
    is_decision_model,
    MAX_REQUEST_BYTES,
    MAX_STATE_BYTES,
    DECISION_MODEL,
    assess_decision,
    build_request,
    parse_response,
)


CANDIDATES = [
    {"id": "sol", "description": "Implementation; subscription.", "local_only": True},
    {"id": "astra", "description": "Complex reasoning; subscription."},
]
EFFORTS = ["low", "medium", "high", "xhigh"]


def response():
    return {
        "model": DECISION_MODEL,
        "answers": {
            "route": {
                "type": "choice",
                "choice": "sol",
                "probabilities": {"sol": 0.96, "astra": 0.04},
                "confidence": 0.75,
            },
            "effort": {
                "type": "choice",
                "choice": "medium",
                "probabilities": {"low": 0.03, "medium": 0.9, "high": 0.05, "xhigh": 0.02},
                "confidence": 0.8,
            },
        },
        "usage": {"input_tokens": 300, "output_tokens": 0, "cost": 0},
    }


def parse(payload):
    return parse_response(payload, ["sol", "astra"], EFFORTS)


class RequestTests(unittest.TestCase):
    def test_valid_request_and_input_isolation(self):
        state = {"summary": "修復 Slint GUI", "constraints": ["bounded task"]}
        original = copy.deepcopy(state)
        payload = build_request(state, CANDIDATES, EFFORTS)
        self.assertEqual(payload["model"], DECISION_MODEL)
        self.assertEqual(payload["state"], original)
        self.assertEqual(set(payload["questions"]), {"route", "effort"})
        self.assertEqual(payload["questions"]["route"]["criteria"], {
            "sol": CANDIDATES[0]["description"], "astra": CANDIDATES[1]["description"]
        })
        self.assertNotIn("provider", payload)
        self.assertNotIn("local_only", json.dumps(payload))
        payload["state"]["constraints"].append("changed")
        self.assertEqual(state, original)

    def test_decision_model_is_fixed_luna_without_a_free_variant(self):
        payload = build_request({"model": "untrusted/other:free"}, CANDIDATES, EFFORTS)
        self.assertEqual(DECISION_MODEL, "openai/gpt-6-luna-decisions")
        self.assertEqual(payload["model"], DECISION_MODEL)
        self.assertNotIn(":free", payload["model"])
        self.assertEqual(set(payload), {"model", "state", "questions"})

    def test_luna_worker_is_distinct_from_the_decider(self):
        candidates = [{"id": "gpt-6-luna", "description": "Ordinary Luna task worker."},
                      {"id": "gpt-6.1-sol", "description": "Implementation worker."}]
        payload = build_request({"summary": "Summarize public text."}, candidates, ["low", "medium"])
        self.assertEqual(payload["model"], "openai/gpt-6-luna-decisions")
        self.assertEqual(payload["questions"]["route"]["type"], "choice")
        self.assertEqual(payload["questions"]["route"]["criteria"]["gpt-6-luna"], "Ordinary Luna task worker.")
        self.assertNotIn(DECISION_MODEL, payload["questions"]["route"]["criteria"])
        self.assertEqual(set(payload["questions"]), {"route", "effort"})

    def test_single_option_questions_are_omitted(self):
        # Live API: "Decisions needs at least 2 choices" (HTTP 400).
        single = [{"id": "gpt-6-astra", "description": "Only eligible worker."}]
        payload = build_request({}, single, ["low", "high"])
        self.assertEqual(set(payload["questions"]), {"effort"})
        answer = {"type": "choice", "choice": "high", "probabilities": {"low": 0.3, "high": 0.7}}
        parsed = parse_response({"model": DECISION_MODEL, "answers": {"effort": answer}},
                                ["gpt-6-astra"], ["low", "high"])
        self.assertEqual((parsed["selected_id"], parsed["effort"]), ("gpt-6-astra", "high"))
        self.assertEqual((parsed["probability"], parsed["margin"]), (1.0, 1.0))
        payload = build_request({}, CANDIDATES, ["medium"])
        self.assertEqual(set(payload["questions"]), {"route"})
        with self.assertRaisesRegex(ValueError, "two_options"):
            build_request({}, single, ["low"])

    def test_invalid_candidates(self):
        invalid = [[], {}, [None], [{}], [{"id": "sol", "description": ""}],
                   [{"id": True, "description": "x"}], [CANDIDATES[0]] * 2]
        for candidates in invalid:
            with self.subTest(candidates=candidates), self.assertRaises(ValueError):
                build_request({}, candidates, EFFORTS)

    def test_candidate_count(self):
        allowed = [{"id": str(i), "description": "task"} for i in range(32)]
        self.assertEqual(len(build_request({}, allowed, EFFORTS)["questions"]["route"]["criteria"]), 32)
        with self.assertRaises(ValueError):
            build_request({}, allowed + [{"id": "32", "description": "task"}], EFFORTS)

    def test_invalid_efforts(self):
        for efforts in ([], ["high", "high"], ["invented"], [True], "low"):
            with self.subTest(efforts=efforts), self.assertRaises(ValueError):
                build_request({}, CANDIDATES, efforts)

    def test_state_byte_limit_including_multibyte_characters(self):
        with self.assertRaisesRegex(ValueError, "state exceeds"):
            build_request({"summary": "中" * (MAX_STATE_BYTES // 3)}, CANDIDATES, EFFORTS)
        # Compact {"x":"..."} JSON adds eight ASCII bytes.
        payload = build_request({"x": "a" * (MAX_STATE_BYTES - 8)}, CANDIDATES, EFFORTS)
        self.assertEqual(len(json.dumps(payload["state"], separators=(",", ":")).encode()), MAX_STATE_BYTES)
        with self.assertRaisesRegex(ValueError, "state exceeds"):
            build_request({"x": "a" * (MAX_STATE_BYTES - 7)}, CANDIDATES, EFFORTS)

    def test_full_request_limit(self):
        with self.assertRaisesRegex(ValueError, "request exceeds"):
            build_request({}, [{"id": "sol", "description": "x" * MAX_REQUEST_BYTES},
                               {"id": "astra", "description": "y"}], EFFORTS)

    def test_non_json_state(self):
        for state in ([], {"number": float("nan")}, {"value": object()}, {"s": "\ud800"}):
            with self.subTest(state=repr(state)), self.assertRaises(ValueError):
                build_request(state, CANDIDATES, EFFORTS)


class ResponseTests(unittest.TestCase):
    def test_response_model_is_preserved_for_logging(self):
        value = response()
        value["model"] = "openai/gpt-6-luna-decisions-snapshot"
        self.assertEqual(parse(value)["model"], value["model"])

    def test_valid_response_keeps_confidence_separate(self):
        result = parse(response())
        self.assertEqual(result["selected_id"], "sol")
        self.assertEqual(result["effort"], "medium")
        self.assertEqual(result["probability"], 0.96)
        self.assertEqual(result["reported_confidence"], 0.75)
        self.assertAlmostEqual(result["margin"], 0.92)
        self.assertEqual(result["effort_probability"], 0.9)

    def test_confidence_is_optional(self):
        value = response()
        del value["answers"]["route"]["confidence"]
        self.assertIsNone(parse(value)["reported_confidence"])

    def test_missing_required_fields(self):
        for section, key in (("root", "model"), ("root", "answers"),
                             ("answers", "route"), ("answers", "effort"),
                             ("route", "type"), ("route", "choice"),
                             ("route", "probabilities"), ("effort", "probabilities")):
            value = response()
            target = value if section == "root" else value["answers"] if section == "answers" else value["answers"][section]
            del target[key]
            with self.subTest(section=section, key=key), self.assertRaises(ValueError):
                parse(value)

    def test_unexpected_or_forged_choice(self):
        for field, choice in (("route", "paid/unapproved"), ("effort", "ultra"), ("route", True)):
            value = response()
            value["answers"][field]["choice"] = choice
            with self.subTest(field=field, choice=choice), self.assertRaises(ValueError):
                parse(value)

    def test_distribution_keys_must_match(self):
        for mode in ("missing", "extra", "renamed"):
            value = response()
            probabilities = value["answers"]["route"]["probabilities"]
            if mode == "missing":
                del probabilities["astra"]
            elif mode == "extra":
                probabilities["rogue"] = 0
            else:
                probabilities["rogue"] = probabilities.pop("astra")
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                parse(value)

    def test_invalid_numbers(self):
        for bad in (True, False, None, "0.96", float("nan"), float("inf"), float("-inf"), -0.01, 1.01):
            for question in ("route", "effort"):
                value = response()
                choice = value["answers"][question]["choice"]
                value["answers"][question]["probabilities"][choice] = bad
                with self.subTest(bad=bad, question=question), self.assertRaises(ValueError):
                    parse(value)

    def test_invalid_confidence(self):
        for question in ("route", "effort"):
            for bad in (True, None, float("nan"), 1.1):
                value = response()
                value["answers"][question]["confidence"] = bad
                with self.subTest(question=question, bad=bad), self.assertRaises(ValueError):
                    parse(value)

    def test_sum_tolerance_and_no_renormalization(self):
        value = response()
        value["answers"]["route"]["probabilities"] = {"sol": 0.96, "astra": 0.02}
        self.assertEqual(parse(value)["probability"], 0.96)
        value["answers"]["route"]["probabilities"]["astra"] = 0.019
        with self.assertRaises(ValueError):
            parse(value)
        value["answers"]["route"]["probabilities"] = {"sol": 0.96, "astra": 0.061}
        with self.assertRaises(ValueError):
            parse(value)

    def test_choice_must_match_maximum(self):
        for question, alternate in (("route", "astra"), ("effort", "low")):
            value = response()
            value["answers"][question]["choice"] = alternate
            with self.subTest(question=question), self.assertRaises(ValueError):
                parse(value)

    def test_ties_and_one_candidate(self):
        value = response()
        value["answers"]["route"]["probabilities"] = {"sol": 0.5, "astra": 0.5}
        self.assertEqual(parse(value)["margin"], 0.0)
        value["answers"]["route"]["probabilities"] = {"sol": 1.0}
        result = parse_response(value, ["sol"], EFFORTS)
        self.assertEqual(result["margin"], 1.0)

    def test_errors_and_wrong_answer_types(self):
        value = response()
        value["error"] = {"message": "failure"}
        with self.assertRaises(ValueError):
            parse(value)
        value = response()
        value["answers"]["route"]["type"] = "noul"
        with self.assertRaises(ValueError):
            parse(value)

    def test_invalid_expected_options(self):
        for candidates, efforts in (([], EFFORTS), (["sol", "sol"], EFFORTS), (["sol", "astra"], ["low", "low"])):
            with self.subTest(candidates=candidates, efforts=efforts), self.assertRaises(ValueError):
                parse_response(response(), candidates, efforts)


class PolicyTests(unittest.TestCase):
    def test_threshold_boundaries(self):
        self.assertEqual(assess_decision({"probability": 0.85, "margin": 0.20, "effort_probability": 0.70}, True), "accepted")
        self.assertEqual(assess_decision({"probability": 0.95, "margin": 0.30, "effort_probability": 0.80}, False), "accepted")
        self.assertEqual(assess_decision({"probability": 0.85, "margin": 0.20, "effort_probability": 0.70}, False), "fallback")

    def test_each_criterion_can_trigger_fallback(self):
        baseline = {"probability": 0.96, "margin": 0.92, "effort_probability": 0.9}
        for low_risk, limits in ((True, {"probability": 0.85, "margin": 0.2, "effort_probability": 0.7}),
                                 (False, {"probability": 0.95, "margin": 0.3, "effort_probability": 0.8})):
            for field, threshold in limits.items():
                value = {**baseline, field: threshold - 0.001}
                with self.subTest(field=field, low_risk=low_risk):
                    self.assertEqual(assess_decision(value, low_risk), "fallback")

    def test_reported_confidence_does_not_replace_probability(self):
        value = parse(response())
        value["reported_confidence"] = 0.01
        self.assertEqual(assess_decision(value, False), "accepted")
        value.update(probability=0.6, reported_confidence=0.999)
        self.assertEqual(assess_decision(value, True), "fallback")

    def test_invalid_policy_inputs(self):
        with self.assertRaises(ValueError):
            assess_decision({"probability": True, "margin": 0.9, "effort_probability": 0.9}, True)
        with self.assertRaises(ValueError):
            assess_decision(parse(response()), "low")

    def test_dated_snapshot_counts_as_luna_decisions(self):
        self.assertTrue(is_decision_model("openai/gpt-6-luna-decisions"))
        self.assertTrue(is_decision_model("openai/gpt-6-luna-decisions-20261006"))
        for other in ("openai/gpt-6-luna-decisions-x", "openai/gpt-6-luna-decisions-2026100",
                      "other/decision-model", "openai/gpt-6-luna", None):
            with self.subTest(other=other):
                self.assertFalse(is_decision_model(other))


if __name__ == "__main__":
    unittest.main()
