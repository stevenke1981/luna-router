"""Offline transient-decision fixtures; never evidence of a live API call."""
import unittest
from unittest.mock import Mock, patch

from decision_retry import decide_with_retry
from openrouter_backend import APIError


class DecisionRetryTests(unittest.TestCase):
    def run_request(self, failures, retry_after=None):
        ledger, sleep = Mock(), Mock()
        attempts = []
        with patch('decision_retry.orb.decide', side_effect=failures) as request:
            try:
                result = decide_with_retry({}, None, {}, ledger, 'same-job',
                                           max_attempts=3, attempts=attempts, sleep=sleep)
            except APIError:
                result = None
        return result, ledger, sleep, request, attempts

    def test_transient_failure_retries_only_decision_and_counts_every_request(self):
        result, ledger, sleep, request, attempts = self.run_request([
            APIError(status=502), APIError(status=503), {'id': 'fixture'}])
        self.assertEqual(result, {'id': 'fixture'})
        self.assertEqual(ledger.reserve.call_count, 3)
        self.assertTrue(all(call.args == ('same-job', 'decision') for call in ledger.reserve.call_args_list))
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])
        self.assertEqual(len(attempts), 3)

    def test_transient_budget_stops_after_three(self):
        result, ledger, sleep, request, attempts = self.run_request([APIError(status=502)] * 3)
        self.assertIsNone(result)
        self.assertEqual(request.call_count, 3)
        self.assertFalse(attempts[-1]['will_retry'])

    def test_auth_quota_transport_or_invalid_response_not_retried(self):
        for status in [None, 200, 400, 401, 402, 403, 404, 429]:
            with self.subTest(status=status):
                _, _, sleep, request, _ = self.run_request([APIError('SECRET', status)])
                self.assertEqual(request.call_count, 1)
                sleep.assert_not_called()

    def test_long_server_backoff_not_shortened(self):
        _, _, sleep, request, attempts = self.run_request([APIError(status=503, retry_after='120')])
        self.assertEqual(request.call_count, 1)
        sleep.assert_not_called()
        self.assertNotIn('SECRET', str(attempts))

    def test_retry_budget_validation_and_quota_rejection_before_post(self):
        for limit in [0, 4, True]:
            with self.assertRaises(ValueError):
                decide_with_retry({}, None, {}, Mock(), 'job', max_attempts=limit)
        ledger = Mock()
        ledger.reserve.side_effect = ValueError('quota fixture')
        with patch('decision_retry.orb.decide') as request, self.assertRaises(ValueError):
            decide_with_retry({}, None, {}, ledger, 'job')
        request.assert_not_called()
