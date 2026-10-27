import argparse
import copy
import contextlib
import io
import os
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import router
from ledger import Ledger


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.config = router.read_json(router.ROOT / 'config.json')
        self.runtime = router.read_json(router.ROOT / 'examples/runtime-demo.json')
        self.text = router.read_json(router.ROOT / 'evidence/openrouter-text-free.json')
        self.decisions = router.read_json(router.ROOT / 'evidence/openrouter-decisions-luna.json')
        self.task = router.normalize_task(router.read_json(router.ROOT / 'examples/task-rust.json'))
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = Ledger(Path(self.temp.name) / 'ledger.db', {**self.config['policy'], 'requests_per_job':100})

    def response(self, request, *args):
        ids = list(request['questions']['route']['criteria'])
        levels = list(request['questions']['effort']['criteria'])
        def answer(values, selected):
            return {'type':'choice','choice':selected,
                    'probabilities':{v: (0.98 if v == selected else 0.02/(len(values)-1)) for v in values},
                    'confidence':0.77}
        return {'model':'openai/gpt-6-luna-decisions','answers':{
            'route':answer(ids, 'gpt-6.1-sol'), 'effort':answer(levels, 'high')},
            'usage':{'cost':0.0002}}

    def plan(self, **kw):
        return router.make_plan(self.task, self.config, self.runtime, self.text, self.decisions,
                                ledger=self.ledger, job='a', **kw)

    def test_valid_decision_and_cache_keep_native_setting_as_requested_only(self):
        with patch.object(router.orb, 'decide', side_effect=self.response) as call:
            result = self.plan(online=True)
            again = self.plan(online=True)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(result['decision_source'], 'luna_decisions')
        self.assertEqual(again['decision_source'], 'luna_decisions_cache')
        self.assertEqual(result['selected_model'], 'gpt-6.1-sol')
        self.assertEqual(result['requested_effort'], 'high')
        self.assertEqual(result['validation_status'], 'not_executed')
        self.assertEqual(result['decision_cost']['reported_cost_usd'],'0.0002')
        self.assertFalse(result['decision_cost']['local_fee_check'])
        self.assertTrue(result['decision_model_called'])
        self.assertFalse(again['decision_model_called'])
        self.assertTrue(again['decision_cost']['cache_hit'])
        self.assertEqual(self.ledger.paid_daily_summary()['reservation_count'], 0)

    def test_forged_choice_cannot_select_model_outside_eligible_set(self):
        def forged(body, *args):
            value = self.response(body)
            value['answers']['route']['choice'] = 'gpt-6.1-astra'
            return value
        with patch.object(router.orb, 'decide', side_effect=forged):
            result = self.plan(online=True)
        self.assertEqual(result['decision_source'], 'local_fallback')
        self.assertEqual(result['selected_model'], 'gpt-6.1-sol')

    def test_no_external_and_offline_do_not_call_luna(self):
        with patch.object(router.orb, 'decide') as call:
            self.plan(online=False)
            self.task['data_policy'] = 'no_external'
            self.plan(online=True)
            call.assert_not_called()

    def test_long_task_can_use_offline_and_pinned_routes(self):
        self.task['data_policy'] = 'public'
        self.task['objective'] = 'A'*30000
        self.assertEqual(self.plan(online=False)['status'], 'planned')
        self.task['requested_model'] = 'gpt-6.1-sol'
        with patch.object(router.orb, 'decide') as call:
            self.assertEqual(self.plan(online=True)['decision_source'], 'explicit_model')
            call.assert_not_called()

    def test_oversize_routing_summary_uses_local_fallback(self):
        self.task['summary'] = 'A'*30000
        with patch.object(router.orb, 'decide') as call:
            result = self.plan(online=True)
            call.assert_not_called()
        self.assertEqual(result['decision_source'], 'local_fallback')

    def test_rate_limit_is_recorded_without_automatic_retry(self):
        with patch.object(router.orb, 'decide', side_effect=router.orb.APIError('HTTP error',429,'60')) as call:
            result = self.plan(online=True)
        self.assertEqual(call.call_count,1)
        self.assertEqual(result['selected_model'], 'gpt-6.1-sol')
        self.assertIn({'http_status':429,'retry_after':'60'}, result['notes'])

    def test_no_local_cost_check_for_openrouter_and_legacy_settings(self):
        self.config['decision']['max_request_cost_usd'] = '0'
        self.config['decision']['max_daily_cost_usd'] = '0'
        def charge(body, *args):
            result = self.response(body)
            result['usage']['cost'] = '1.23456789'
            return result
        with patch.object(router.orb, 'decide', side_effect=charge):
            plan=self.plan(online=True)
        self.assertEqual(plan['decision_source'],'luna_decisions')
        self.assertEqual(plan['decision_cost']['reported_cost_usd'],'1.23456789')
        self.assertEqual(plan['decision_cost']['cost_owner'],'openrouter')
        self.assertEqual(self.ledger.paid_daily_summary()['reservation_count'], 0)

    def test_missing_or_malformed_usage_cost_not_a_fee_gate(self):
        for value in (None, 'NaN', '0.0000000001'):
            def respond(body, *args):
                result = self.response(body)
                result['usage'] = {} if value is None else {'cost':value}
                return result
            with self.subTest(value=value), patch.object(router.orb,'decide',side_effect=respond):
                plan = self.plan(online=True)
            self.assertEqual(plan['decision_source'],'luna_decisions')
            self.assertFalse(plan['decision_cost']['local_fee_check'])
            with self.ledger.connect() as db:
                db.execute('DELETE FROM decisions')
            self.assertEqual(plan['decision_cost']['reported_cost_usd'], None if value in (None,'NaN') else '0.0000000001')

    def test_failed_post_shows_attempted_not_successful(self):
        with patch.object(router.orb, 'decide', side_effect=router.orb.APIError('transport failure')):
            result=self.plan(online=True)
        self.assertEqual(result['decision_source'], 'local_fallback')
        self.assertTrue(result['decision_call_attempted'])
        self.assertFalse(result['decision_model_called'])
        self.assertEqual(self.ledger.paid_daily_summary()['reservation_count'], 0)

    def test_no_luna_model_does_not_fall_back_to_mercury(self):
        self.decisions={'data':[]}
        with patch.object(router.orb,'decide') as call:
            result=self.plan(online=True)
        call.assert_not_called()
        self.assertEqual(result['decision_source'],'local_fallback')
        self.assertEqual(result['decision_model'],'openai/gpt-6-luna-decisions')

    def test_public_catalog_cli_output_can_be_used_as_offline_input(self):
        path = Path(self.temp.name)/'catalog.json'
        router.save_json(path, {'text':self.text,'decisions':self.decisions})
        args = argparse.Namespace(demo=False, command='plan', online=False, runtime=None,catalog_file=str(path))
        _,text,_,_ = router.collect(args,self.task)
        self.assertEqual(text,self.text)

    def test_demo_never_executes(self):
        plan = self.plan(online=False)
        with patch.object(router.codex_backend,'run_task') as run:
            result = router.execute_plan(plan,self.task,self.runtime,None,self.ledger,'a')
            run.assert_not_called()
        self.assertEqual(result['reason'],'simulation_cannot_execute')

    def test_unavailable_exact_pin_is_blocked(self):
        self.task['requested_model'] = 'gpt-6.1-astra'
        self.assertEqual(self.plan(online=False)['status'],'blocked')

    def test_database_failure_blocks_cli_without_model_request(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}), \
             patch.object(router, 'collect', return_value=(self.runtime,self.text,self.decisions,[])), \
             patch.object(router, 'Ledger', side_effect=sqlite3.OperationalError('database is locked')), \
             patch.object(router.orb, 'decide') as decide, \
             contextlib.redirect_stderr(io.StringIO()) as stderr:
            code = router.main(['plan','--online','--task',str(router.ROOT/'examples/task-rust.json')])
        self.assertEqual(code,2)
        self.assertIn('"status": "blocked"',stderr.getvalue())
        decide.assert_not_called()


if __name__ == '__main__':
    unittest.main()
