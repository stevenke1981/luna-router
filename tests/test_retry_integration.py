"""End-to-end simulated workflow; no Codex CLI or paid inference required."""
import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import router
from escalation import RetryStore


class AutomaticRetryIntegrationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.config = router.read_json(router.ROOT / "config.json")
        self.runtime = router.read_json(router.ROOT / "examples/runtime-demo.json")
        self.runtime["simulation"] = False
        for item in self.runtime["models"]:
            item["efforts"].append("max")
        self.catalog = router.read_json(router.ROOT / "evidence/openrouter-text-free.json")
        self.decision = router.read_json(router.ROOT / "evidence/openrouter-decisions-luna.json")
        self.task = router.normalize_task({"objective": "Fix the test error in this project", "kind":"coding", "complexity":2, "needs_tools":True})
        self.verifier = self.root / "check.json"
        self.verifier.write_text(json.dumps({"argv":["python","-c","raise SystemExit(1)"],"timeout_seconds":30}))
        self.args = argparse.Namespace(
            project=str(self.root), state_dir=str(self.root), job_id="codex-retry",
            auto_retry=True, max_attempts=4, verify_argv_file=str(self.verifier), sandbox="workspace-write",
        )
        self.initial = {"status":"planned","simulation":False,"selected_model":"gpt-6.1-sol","backend":"codex",
                        "requested_effort":"medium","decision_source":"luna_decisions",
                        "decision_model_called":True, "worker":{"id":"gpt-6.1-sol", "backend":"codex", "model_id":"gpt-6.1-sol"}}
        self.store = RetryStore(self.root / "escalation.sqlite")

    def run_retries(self, n, *, outcome="failed"):
        self.args.max_attempts=n
        with patch.object(router, "execute_plan", return_value={"status":"turn_completed"}) as worker, \
             patch.object(router, "run_check", return_value={"outcome":outcome,"evidence_kind":"verifier","exit_code":1}) as checks, \
             patch.object(router, "make_plan", return_value=self.initial) as decision:
            result=router.retry_run(self.args,self.task,self.config,self.runtime,self.catalog,self.decision,
                                    ledger=None, job="count-key", notes=[])
        return result, worker.call_count, decision.call_count

    def test_failure_escalation_through_native_max_then_stronger_model(self):
        first, calls, route_calls=self.run_retries(4)
        self.assertEqual((calls, route_calls), (4, 1))
        self.assertEqual([(i['model'],i['effort']) for i in first['attempt_history']], [
            ('gpt-6.1-sol','medium'),('gpt-6.1-sol','medium'),
            ('gpt-6.1-sol','high'),('gpt-6.1-sol','high')])
        self.assertEqual(first['retry']['route']['effort'], 'xhigh')
        self.assertEqual(first['attempt_history'][0]['outcome'], 'failed')
        self.assertTrue(first['initial_plan']['decision_model_called'])
        self.assertFalse(first['plan']['decision_model_called'])
        second, calls, route_calls=self.run_retries(4)
        self.assertEqual((calls,route_calls),(4,0))
        self.assertEqual([(i['model'],i['effort']) for i in second['attempt_history']], [
            ('gpt-6.1-sol','xhigh'),('gpt-6.1-sol','xhigh'),
            ('gpt-6.1-sol','max'),('gpt-6.1-sol','max')])
        self.assertEqual(second['retry']['route']['model'], 'gpt-6-astra')
        self.assertEqual(second['retry']['route']['effort'], 'medium')
        self.assertEqual(second['retry']['total_failures'],8)
        self.assertEqual(second['retry']['last_transition'], 'tier_up:sol->astra')

    def test_next_worker_receives_only_authored_qa_context(self):
        with patch.object(router, "execute_plan", return_value={"status":"turn_completed"}) as worker, \
             patch.object(router, "run_check", return_value={"outcome":"failed","evidence_kind":"verifier","exit_code":1}), \
             patch.object(router, "make_plan", return_value=self.initial):
            router.retry_run(self.args,self.task,self.config,self.runtime,self.catalog,self.decision,
                             ledger=None,job="count-key",notes=[])
        self.assertEqual(worker.call_count,4)
        self.assertEqual(worker.call_args_list[0].args[1]['objective'], self.task['objective'])
        self.assertIn('QA retry context', worker.call_args_list[1].args[1]['objective'])
        self.assertNotIn('Bearer', worker.call_args_list[1].args[1]['objective'])

    def test_fallback_is_blocked_when_worker_failed(self):
        with patch.object(router, "execute_plan", return_value={"status":"failed","error":"chatgpt_subscription_auth_required"}), \
             patch.object(router, "make_plan", return_value=self.initial):
            result=router.retry_run(self.args,self.task,self.config,self.runtime,self.catalog,self.decision,
                                    ledger=None,job="count-key",notes=[])
        self.assertEqual(result['retry']['status'], 'paused')
        self.assertEqual(result['retry']['total_failures'], 0)
        self.assertEqual(len(result['attempt_history']), 1)

    def test_unverified_requires_manual_feedback_not_autoretry(self):
        self.args.auto_retry=False
        self.args.verify_argv_file=None
        with patch.object(router, "execute_plan", return_value={"status":"turn_completed"}), \
             patch.object(router, "make_plan", return_value=self.initial):
            first=router.retry_run(self.args,self.task,self.config,self.runtime,self.catalog,self.decision,
                                    ledger=None,job="count-key",notes=[])
        self.assertEqual(first['retry']['status'], 'needs_verification')
        self.assertEqual(first['retry']['total_failures'],0)
        attempt = first['attempt_history'][-1]['attempt_id']
        from escalation import task_fingerprint
        resumed=self.store.record_outcome('codex-retry', attempt, 'failed', self.task,
                                          fingerprint=task_fingerprint(self.task,self.root), evidence_kind='manual',
                                          evidence_sha256='f'*64, from_review=True)
        self.assertEqual((resumed['status'],resumed['failures']),("ready",1))
        self.assertEqual(router.resume_plan(resumed,self.task,self.config,self.runtime,self.catalog)['decision_source'], 'retry_state')

    def test_job_change_refuses_instead_of_replacing_previous_state(self):
        self.run_retries(2)
        self.task['objective']='a different task'
        with patch.object(router,'make_plan') as call:
            with self.assertRaisesRegex(ValueError, 'another task'):
                router.retry_run(self.args,self.task,self.config,self.runtime,self.catalog,self.decision,
                                 ledger=None,job='count-key',notes=[])
            call.assert_not_called()


if __name__ == '__main__':
    unittest.main()
