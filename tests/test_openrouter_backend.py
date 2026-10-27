"""Offline boundary tests; no test performs an inference or external request."""

from __future__ import annotations

import copy
import io
import json
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import openrouter_backend as backend


def model(**overrides):
    value = {
        "id": "example/worker:free",
        "pricing": {"prompt": "0", "completion": "0"},
        "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
        "context_length": 32768,
        "expiration_date": None,
    }
    value.update(overrides)
    return value


def decision_policy(**overrides):
    result = {"model": backend.DECISION_MODEL, "enabled": True}
    result.update(overrides)
    return result


def decision_model(**overrides):
    result = {
        "id": backend.DECISION_MODEL,
        "pricing": {
            "prompt": "0.0000001", "completion": "0", "web_search": "0.01",
            "input_cache_read": "0", "input_cache_write": "0",
        },
        "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["decisions"]},
        "context_length": 1050000,
        "expiration_date": None,
    }
    result.update(overrides)
    return result


def decision_body(**overrides):
    result = {
        "model": backend.DECISION_MODEL,
        "state": "task",
        "questions": {"route": {"type": "choice", "criteria": {"a": "A", "b": "B"}}},
    }
    result.update(overrides)
    return result


class FakeResponse(io.BytesIO):
    def __init__(self, body, url=backend.BASE_URL + "/api/v1/models", status=200, headers=None):
        super().__init__(body)
        self.url = url
        self.status = status
        self.headers = headers or {}

    def geturl(self):
        return self.url


class FreeRecordTests(unittest.TestCase):
    def test_zero_price_strings_and_alias(self):
        for price in ["0", "0.0", "0e-10", 0, 0.0]:
            with self.subTest(price=price):
                row = model(pricing={"prompt": price, "completion": price, "request": price})
                self.assertTrue(backend.valid_free(row))
        self.assertTrue(backend.valid_free(model(id="openrouter/free")))

    def test_nonzero_negative_nonfinite_and_unknown_prices_fail_closed(self):
        for price in ["0.01", "-0.01", "NaN", "Infinity", "-Infinity", float("nan"),
                      float("inf"), "abc", "", None, False, {}, []]:
            with self.subTest(price=price):
                self.assertFalse(backend.valid_free(model(pricing={"prompt": "0", "completion": price})))
        self.assertFalse(backend.valid_free(model(pricing={"prompt": "0", "completion": "0", "image": "1"})))
        self.assertFalse(backend.valid_free(model(pricing={"prompt": "0", "completion": "0", "overrides": []})))

    def test_required_prices_and_ids(self):
        for pricing in [{}, {"prompt": "0"}, {"completion": "0"}, None]:
            self.assertFalse(backend.valid_free(model(pricing=pricing)))
        for identifier in ["example/worker", "example/worker:free ", "example/worker:free:online",
                           "https://example/worker:free", "example/worker:free\n", None]:
            self.assertFalse(backend.valid_free(model(id=identifier)))

    def test_context_and_modalities(self):
        for context in [0, -1, None, "32768", True, 32768.0]:
            self.assertFalse(backend.valid_free(model(context_length=context)))
        row = model(id="inception/mercury-decide:free", architecture={"output_modalities": ["decisions"]})
        self.assertFalse(backend.valid_free(row))
        self.assertTrue(backend.valid_free(row, "decisions"))
        self.assertFalse(backend.valid_free(model(architecture={"output_modalities": "text"})))
        self.assertFalse(backend.valid_free(model(), "all"))

    def test_expiration(self):
        now = datetime.now(timezone.utc)
        self.assertTrue(backend.valid_free(model(expiration_date=(now + timedelta(days=2)).date().isoformat())))
        self.assertTrue(backend.valid_free(model(expiration_date=(now + timedelta(hours=2)).isoformat())))
        for expiration in [(now - timedelta(days=1)).date().isoformat(),
                           (now - timedelta(seconds=1)).isoformat(), "tomorrow", "",
                           "2030-01-01T12:00:00", 20300101, "2030-02-30"]:
            self.assertFalse(backend.valid_free(model(expiration_date=expiration)))

    def test_filter_preserves_real_records(self):
        allowed = model()
        denied = model(id="example/paid")
        self.assertEqual(backend.get_free_models({"data": [denied, allowed]}), [allowed])
        with self.assertRaises(ValueError):
            backend.get_free_models({"models": []})


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_public_catalog_does_not_need_or_send_token(self):
        with patch.object(backend, "build_opener") as build:
            build.return_value.open.return_value = FakeResponse(b'{"data": []}')
            with patch.dict("os.environ", {"OPENROUTER_API_KEY": "never-send-to-public-catalog"}):
                self.assertEqual(backend.request_json("GET", "/api/v1/models"), {"data": []})
            req = build.return_value.open.call_args.args[0]
            self.assertIsNone(req.get_header("Authorization"))
            self.assertIsInstance(build.call_args.args[0], backend._NoRedirect)

    def test_post_requires_environment_key_and_does_not_run_when_missing(self):
        empty = {"OPENROUTER_API_KEY": "", "LUNA_ROUTER_IGNORE_REGISTRY_KEY": "1"}
        with patch.dict("os.environ", empty), patch.object(backend, "build_opener") as build:
            with self.assertRaises(backend.APIError):
                backend.request_json("POST", "/api/alpha/decisions", {})
            build.assert_not_called()

    def test_authenticated_post(self):
        path = "/api/alpha/decisions"
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-token"}), patch.object(backend, "build_opener") as build:
            build.return_value.open.return_value = FakeResponse(b'{"answers": {}}', backend.BASE_URL + path)
            backend.request_json("POST", path, {"model": backend.DECISION_MODEL})
            req = build.return_value.open.call_args.args[0]
            self.assertEqual(req.get_header("Authorization"), "Bearer test-token")
            self.assertEqual(json.loads(req.data), {"model": backend.DECISION_MODEL})
            self.assertEqual(req.get_header("User-agent"), "adaptive-luna-router/2.7.0")
            build.return_value.open.assert_called_once()

    def stale_key_call(self, status, registry_value):
        path = "/api/alpha/decisions"
        def opener(req, timeout):
            if req.get_header("Authorization") == "Bearer stale-token":
                raise HTTPError(backend.BASE_URL + path, status, "x", {}, None)
            return FakeResponse(b'{"answers": {}}', backend.BASE_URL + path)
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "stale-token"}), \
             patch.object(backend, "registry_key", return_value=(registry_value, "user_environment")), \
             patch.object(backend, "build_opener") as build:
            build.return_value.open.side_effect = opener
            try:
                return backend.request_json("POST", path, {"model": backend.DECISION_MODEL}), build
            except backend.APIError as exc:
                return exc, build

    def test_stale_process_key_401_retries_once_with_live_registry_key(self):
        result, build = self.stale_key_call(401, "fresh-token")
        self.assertEqual(result, {"answers": {}})
        sent = [call.args[0].get_header("Authorization") for call in build.return_value.open.call_args_list]
        self.assertEqual(sent, ["Bearer stale-token", "Bearer fresh-token"])

    def test_no_retry_when_registry_key_is_same_or_error_is_not_401(self):
        for status, registry_value in ((401, "stale-token"), (401, ""), (402, "fresh-token")):
            with self.subTest(status=status, registry=registry_value):
                result, build = self.stale_key_call(status, registry_value)
                self.assertIsInstance(result, backend.APIError)
                self.assertEqual(result.status, status)
                build.return_value.open.assert_called_once()

    def test_external_url_and_redirect_paths_are_rejected(self):
        for path in ["https://evil.invalid/api/v1/models", "//evil.invalid/api/v1/models",
                     "/api/../v1/models", "/api/v1/models#fragment", "/api/\\evil", "/api/\nevil"]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                backend.request_json("GET", path)
        self.assertIsNone(backend._NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.invalid"))

    def test_redirect_responses_fail_without_retry(self):
        with patch.object(backend, "build_opener") as build:
            build.return_value.open.side_effect = HTTPError(
                backend.BASE_URL + "/api/v1/models", 302, "redirect", {"Location": "https://evil.invalid"}, None
            )
            with self.assertRaises(backend.APIError) as result:
                backend.request_json("GET", "/api/v1/models")
            self.assertEqual(result.exception.status, 302)
            build.return_value.open.assert_called_once()
        with patch.object(backend, "build_opener") as build:
            build.return_value.open.return_value = FakeResponse(b'{"data": []}', "https://evil.invalid")
            with self.assertRaises(backend.APIError):
                backend.request_json("GET", "/api/v1/models")

    def test_http_errors_are_sanitized_and_retry_after_preserved(self):
        for status in [400, 429]:
            with self.subTest(status=status), patch.object(backend, "build_opener") as build:
                build.return_value.open.side_effect = HTTPError(
                    backend.BASE_URL + "/api/v1/models", status, "SECRET provider payload",
                    {"Retry-After": "15"}, io.BytesIO(b'SECRET token body')
                )
                with self.assertRaises(backend.APIError) as result:
                    backend.request_json("GET", "/api/v1/models")
                self.assertEqual(result.exception.status, status)
                self.assertEqual(result.exception.retry_after, "15")
                self.assertNotIn("SECRET", str(result.exception))
                build.return_value.open.assert_called_once()

    def test_http_200_error_is_not_a_success(self):
        with patch.object(backend, "build_opener") as build:
            build.return_value.open.return_value = FakeResponse(b'{"error": {"code": 429, "message": "SECRET"}}')
            with self.assertRaises(backend.APIError) as result:
                backend.request_json("GET", "/api/v1/models")
            self.assertEqual(result.exception.status, 429)
            self.assertNotIn("SECRET", str(result.exception))

    def test_nonfinite_or_duplicate_or_invalid_json_is_rejected(self):
        for body in [b'{"x": NaN}', b'{"x": Infinity}', b'{"x": 1e9999}',
                     b'{"x": 1, "x": 2}', b'[]', b'not json', b'\xff']:
            with self.subTest(body=body), patch.object(backend, "build_opener") as build:
                build.return_value.open.return_value = FakeResponse(body)
                with self.assertRaises(backend.APIError):
                    backend.request_json("GET", "/api/v1/models")

    def test_transport_error_does_not_expose_details(self):
        with patch.object(backend, "build_opener") as build:
            build.return_value.open.side_effect = URLError("SECRET credential")
            with self.assertRaises(backend.APIError) as result:
                backend.request_json("GET", "/api/v1/models")
            self.assertIsNone(result.exception.status)
            self.assertNotIn("SECRET", str(result.exception))

    def test_nonfinite_outgoing_payload_is_rejected_before_network(self):
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-token"}), patch.object(backend, "build_opener") as build:
            with self.assertRaises(backend.APIError):
                backend.request_json("POST", "/api/alpha/decisions", {"score": float("nan")})
            build.assert_not_called()


class AdapterTests(unittest.TestCase):
    def test_catalog_marks_real_source_and_uses_full_modality_catalog(self):
        with patch.object(backend, "request_json", return_value={"data": [model()], "links": {"next": None}}) as request:
            result = backend.catalog("decisions")
            request.assert_called_once_with("GET", "/api/v1/models?output_modalities=decisions")
            self.assertFalse(result["simulation"])
            self.assertNotIn("q=free", result["source"])
            self.assertTrue(result["fetched_at"].endswith("Z"))

    def test_catalog_rejects_unexpected_pagination(self):
        with patch.object(backend, "request_json", return_value={"data": [], "links": {"next": "next"}}):
            with self.assertRaises(backend.APIError):
                backend.catalog()

    def response(self, content="done", finish="stop", calls=None):
        return {
            "model": "example/worker-20261026",
            "choices": [{"message": {"content": content, "tool_calls": calls}, "finish_reason": finish}],
            "usage": {"cost": 0, "prompt_tokens": 10, "completion_tokens": 1},
        }

    def test_free_completion_is_single_model_with_no_tools_or_paid_fallback(self):
        with patch.object(backend, "request_json", return_value=self.response()) as request:
            result = backend.complete(model(), "Summarize this text")
            method, path, payload = request.call_args.args
            self.assertEqual((method, path), ("POST", "/api/v1/chat/completions"))
            self.assertEqual(payload["model"], "example/worker:free")
            for forbidden in ["models", "plugins", "tools", "tool_choice"]:
                self.assertNotIn(forbidden, payload)
            self.assertEqual(payload["provider"], backend._free_provider())
            self.assertEqual(result["requested_model"], "example/worker:free")
            self.assertEqual(result["actual_model"], "example/worker-20261026")
            self.assertEqual(result["status"], "response_received")
            self.assertFalse(result["tool_calls_present"])

    def test_completion_requires_free_model_and_context_headroom(self):
        with patch.object(backend, "request_json") as request:
            for row, text, max_tokens in [
                (model(id="example/paid"), "task", 2048),
                (model(context_length=8000), "繁" * 1000, 2048),
                (model(), "task", True),
                (model(top_provider={"max_completion_tokens": 100}), "task", 200),
            ]:
                with self.subTest(row=row), self.assertRaises(ValueError):
                    backend.complete(row, text, max_tokens)
            request.assert_not_called()

    def test_partial_outputs_are_distinguished(self):
        cases = [
            ("cut off", "length", None, "incomplete_length"),
            (None, "tool_calls", [{"id": "call_1"}], "tool_calls_received"),
            (" ", "stop", None, "empty_response"),
            ("partial", None, None, "incomplete_response"),
            ("blocked", "content_filter", None, "incomplete_response"),
        ]
        for content, finish, calls, expected in cases:
            with self.subTest(expected=expected), patch.object(backend, "request_json", return_value=self.response(content, finish, calls)):
                self.assertEqual(backend.complete(model(), "task")["status"], expected)

    def test_reported_nonzero_free_usage_is_warning_not_local_spend_gate(self):
        response = self.response()
        response["usage"]["cost"] = "0.01"
        with patch.object(backend, "request_json", return_value=response):
            result = backend.complete(model(), "task")
        self.assertEqual(result["status"], "response_received")
        self.assertEqual(result["reported_cost_usd"], "0.01")
        self.assertIn("provider_reported_nonzero_cost_for_free_worker", result["warnings"])

    def test_decide_delegates_paid_spend_limits_to_openrouter_without_mutation(self):
        body = decision_body(provider={"data_collection": "deny", "zdr": True})
        before = copy.deepcopy(body)
        with patch.object(backend, "request_json", return_value={"answers": {}}) as request:
            backend.decide(body, decision_model(), decision_policy())
            payload = request.call_args.args[2]
            self.assertEqual(request.call_args.args[:2], ("POST", "/api/alpha/decisions"))
            self.assertNotIn("max_price", payload["provider"])
            self.assertTrue(payload["provider"]["require_parameters"])
            self.assertEqual(payload["provider"]["data_collection"], "deny")
            self.assertTrue(payload["provider"]["zdr"])
            self.assertEqual(payload["model"], backend.DECISION_MODEL)
            request.assert_called_once()
        self.assertEqual(body, before)
        for bad in [{**body, "model": "inception/mercury-decide:free"}, {**body, "models": ["paid/model"]}]:
            with self.assertRaises(ValueError):
                backend.decide(bad, decision_model(), decision_policy())

    def test_quota_returns_only_daily_counts(self):
        quota = {"used": 3, "limit": 50, "remaining": 47}
        response = {"data": {"label": "SECRET", "usage": 123, "free_model_daily_requests": {**quota, "extra": "SECRET"}}}
        with patch.object(backend, "request_json", return_value=response) as request:
            self.assertEqual(backend.get_quota(), quota)
            request.assert_called_once_with("GET", "/api/v1/key")


class PaidDecisionTests(unittest.TestCase):
    """OpenRouter spending remains under the provider's key/account controls."""

    def test_luna_is_available_but_not_in_free_pool(self):
        row = decision_model()
        listing = {"data": [model(), row]}
        self.assertIs(backend.get_decision_model(listing, decision_policy()), row)
        self.assertFalse(backend.valid_free(row, "decisions"))
        self.assertEqual(backend.get_free_models(listing), [listing["data"][0]])

    def test_exact_model_only_no_mercury_fallback(self):
        listing = {"data": [model(id="inception/mercury-decide:free")]}
        self.assertIsNone(backend.get_decision_model(listing, decision_policy()))
        for wrong in ["inception/mercury-decide:free", "openai/gpt-6-luna", "openai/gpt-6-luna-decisions:free"]:
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                backend.get_decision_model(listing, decision_policy(model=wrong))

    def test_no_price_gate_even_if_old_config_fields_present(self):
        legacy = decision_policy(max_request_cost_usd="0", max_daily_cost_usd="0", max_prompt_price_per_million_usd="0")
        for prices in [None, {"prompt": "1", "completion": "3", "request": "8"},
                       {"prompt": "0.0000002", "completion": "0.0000004"},
                       {"prompt": "NaN"}, {"overrides": []}]:
            with self.subTest(prices=prices):
                row = decision_model(pricing=prices)
                self.assertEqual(backend.get_decision_model({"data": [row]}, legacy), row)
                with patch.object(backend, "request_json", return_value={"answers": {}, "usage": {"cost": "100"}}) as call:
                    self.assertEqual(backend.decide(decision_body(), row, legacy)["usage"]["cost"], "100")
                    self.assertNotIn("max_price", call.call_args.args[2]["provider"])

    def test_invalid_identity_or_capability_rejected(self):
        invalids = [
            decision_model(architecture={"input_modalities": [], "output_modalities": ["decisions"]}),
            decision_model(architecture={"input_modalities": ["text"], "output_modalities": ["text"]}),
            decision_model(architecture=None),
            decision_model(context_length=0), decision_model(context_length=True),
            decision_model(expiration_date="not-a-date"),
            decision_model(expiration_date="2000-01-01"),
        ]
        for row in invalids:
            with self.subTest(row=row), self.assertRaises(ValueError):
                backend.get_decision_model({"data": [row]}, decision_policy())
        self.assertIsNone(backend.get_decision_model({"data": [decision_model(id="a/b")]}, decision_policy()))
        with self.assertRaises(ValueError):
            backend.get_decision_model({"data": [decision_model(), decision_model()]}, decision_policy())

    def test_disabled_policy_or_bad_catalog_rejected(self):
        for cfg in [decision_policy(enabled=False), decision_policy(model="wrong")]:
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                backend.get_decision_model({"data": [decision_model()]}, cfg)
        with self.assertRaises(ValueError):
            backend.get_decision_model({"data": ["oops"]}, decision_policy())

    def test_decision_privacy_preferences_preserved_and_tools_rejected(self):
        with patch.object(backend, "request_json", return_value={"answers": {}}) as call:
            backend.decide(decision_body(provider={"data_collection":"deny", "zdr":True}), decision_model(), decision_policy())
            out = call.call_args.args[2]
            self.assertEqual(out["provider"], {"data_collection":"deny", "zdr":True, "allow_fallbacks":True, "require_parameters":True})
            for forbidden in ["max_price", "tools", "plugins", "web_search", "models"]:
                self.assertNotIn(forbidden, out)
        for key in ["models", "plugins", "tools", "web_search", "tool_choice"]:
            for bad in (decision_body(**{key: []}), decision_body(provider={key: []})):
                with self.subTest(key=key), patch.object(backend, "request_json") as request:
                    with self.assertRaises(ValueError):
                        backend.decide(bad, decision_model(), decision_policy())
                    request.assert_not_called()
        for bad in (decision_body(provider={"max_price":{"prompt":0.01}}), decision_body(provider={"zdr":"true"})):
            with patch.object(backend, "request_json") as req:
                with self.assertRaises(ValueError):
                    backend.decide(bad, decision_model(), decision_policy())
                req.assert_not_called()

    def test_request_size_question_and_context_limits_are_not_price_checks(self):
        cases = [
            (decision_body(state="x" * (48 * 1024)), decision_model()),
            (decision_body(questions={}), decision_model()),
            (decision_body(questions={str(i): {"type":"noul"} for i in range(33)}), decision_model()),
            (decision_body(questions={"route": {"type": []}}), decision_model()),
            (decision_body(), decision_model(context_length=4096)),
            (decision_body(state={"value": float("nan")}), decision_model()),
        ]
        for body, row in cases:
            with self.subTest(body=str(body)[:50]), patch.object(backend, "request_json") as request:
                with self.assertRaises(ValueError):
                    backend.decide(body, row, decision_policy())
                request.assert_not_called()

    def test_usage_cost_is_observational_and_never_blocks_decisions(self):
        costs = [0, "0.00012", 0.00012, "0.05", "100", -1, "NaN", None, True, "unknown"]
        for cost in costs:
            with self.subTest(cost=cost), patch.object(backend, "request_json", return_value={"answers": {}, "usage": {"cost": cost}}) as request:
                value = backend.decide(decision_body(), decision_model(), decision_policy())
                self.assertEqual(value["usage"]["cost"], cost)
                if isinstance(cost, (str, int, float)) and not isinstance(cost, bool):
                    if str(cost).replace('.','',1).isdigit():
                        self.assertIsNotNone(backend.reported_cost(value))
                request.assert_called_once()
        self.assertIsNone(backend.reported_cost({"usage": {"cost": "NaN"}}))
        self.assertIsNone(backend.reported_cost({"usage": {"cost": -1}}))

    def test_missing_cost_is_unknown_but_valid_response(self):
        for response in [{"answers": {}}, {"answers": {}, "usage": {}}, {"answers": {}, "usage": None}]:
            with patch.object(backend, "request_json", return_value=response):
                self.assertEqual(backend.decide(decision_body(), decision_model(), decision_policy()), response)
                self.assertIsNone(backend.reported_cost(response))

    def test_invalid_success_json_does_not_retry(self):
        with patch.object(backend, "request_json", side_effect=backend.APIError("invalid JSON", status=200)) as request:
            with self.assertRaises(backend.APIError):
                backend.decide(decision_body(), decision_model(), decision_policy())
            request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
