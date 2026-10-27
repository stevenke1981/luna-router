"""Local expense checker applies only to an explicit future OpenAI direct route."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from pathlib import Path

import cost_control
from ledger import BudgetExceeded
import router


class ExpenseIsolationTests(unittest.TestCase):
    def setUp(self):
        self.config = router.read_json(router.ROOT / 'config.json')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / 'new-state'

    def test_no_check_openrouter_even_with_bad_missing_cost_configuration(self):
        for cfg in ({}, {'fee_control': {}}, {'fee_control': {'openai_router': {'enabled': False}}},
                    {'fee_control': {'openai_router': {'enabled': True, 'max_request_cost_usd': 'NaN'}}}):
            with self.subTest(cfg=cfg):
                self.assertIsNone(cost_control.guard_for_backend('openrouter', cfg, state_dir=self.state))
        self.assertFalse(self.state.exists(), 'OpenRouter must not open a local cost DB')

    def test_no_check_codex_subscription(self):
        self.assertIsNone(cost_control.guard_for_backend('codex', {}, state_dir=self.state))
        self.assertFalse(self.state.exists())

    def test_unsupported_backend_is_not_silently_exempt(self):
        for backend in ('openai', 'unknown', '', None, 'openai_api'):
            with self.subTest(backend=backend), self.assertRaises(cost_control.FeeConfigurationError):
                cost_control.guard_for_backend(backend, self.config, state_dir=self.state)

    def test_openai_direct_requires_enabled_fee_settings(self):
        for cfg in ({}, {'fee_control': {}},
                    {'fee_control': {'openai_router': {'enabled': False}}},
                    {'fee_control': {'openai_router': {'enabled': True, 'max_request_cost_usd': '0.01'}}}):
            with self.subTest(cfg=cfg), self.assertRaises(cost_control.FeeConfigurationError):
                cost_control.guard_for_backend('openai_router', cfg, state_dir=self.state)

    def test_openai_direct_reserves_settles_and_limits_spend(self):
        config = router.read_json(router.ROOT / 'config.json')
        config['fee_control']['openai_router']['max_daily_cost_usd'] = '0.015'
        ctl = cost_control.guard_for_backend('openai_router', config, state_dir=self.state)
        rid = ctl.reserve('first')
        self.assertIsInstance(rid, int)
        with self.assertRaisesRegex(BudgetExceeded, 'max_daily_cost_usd'):
            ctl.reserve('second')
        settled = ctl.settle(rid, '0.002')
        self.assertEqual(settled['actual_cost_usd'], '0.002000000')
        ctl.reserve('second')
        self.assertEqual(ctl.summary()['reservation_count'], 2)
        self.assertEqual(ctl.summary()['reserved_usd'], '0.010000000')

    def test_openai_direct_invalid_money_is_blocked_before_dispatch(self):
        config = router.read_json(router.ROOT / 'config.json')
        config['fee_control']['openai_router']['max_request_cost_usd'] = 'NaN'
        ctl = cost_control.guard_for_backend('openai_router', config, state_dir=self.state)
        with self.assertRaises(ValueError):
            ctl.reserve('job')

    def test_openai_unknown_cost_retains_reservation(self):
        ctl = cost_control.guard_for_backend('openai_router', self.config, state_dir=self.state)
        rid = ctl.reserve('job')
        ctl.settle(rid, None)
        self.assertEqual(ctl.summary()['reserved_usd'], '0.010000000')
        self.assertEqual(ctl.summary()['unknown_count'], 1)

    def test_openai_direct_has_separate_database_from_openrouter(self):
        ctl = cost_control.guard_for_backend('openai_router', self.config, state_dir=self.state)
        self.assertTrue((self.state / 'openai_costs.sqlite').is_file())
        self.assertFalse((self.state / 'usage.sqlite').exists())
        self.assertIsNone(cost_control.guard_for_backend('openrouter', self.config, state_dir=self.state))

    def test_parallel_openai_fee_budget_is_atomic(self):
        config = router.read_json(router.ROOT / 'config.json')
        config['fee_control']['openai_router']['max_daily_cost_usd'] = '0.01'
        config['policy']['requests_per_job'] = 20
        ctl = cost_control.guard_for_backend('openai_router', config, state_dir=self.state)
        def run(i):
            try:
                return ('approved', ctl.reserve(f'job-{i}'))
            except BudgetExceeded:
                return ('denied', None)
        with ThreadPoolExecutor(max_workers=6) as ex:
            result = list(ex.map(run, range(6)))
        self.assertEqual([x[0] for x in result].count('approved'), 1)

    def test_fee_status_reveals_no_keys_or_fake_verified_account_limits(self):
        value = cost_control.describe_fees(self.config)
        self.assertFalse(value['openrouter']['local_fee_check'])
        self.assertFalse(value['openrouter']['account_limit_verified'])
        self.assertTrue(value['openai_router']['local_fee_check_required_if_enabled'])
        self.assertFalse(value['openai_router']['implemented'])
        self.assertNotIn('OPENROUTER_API_KEY', str(value))


if __name__ == '__main__':
    unittest.main()
