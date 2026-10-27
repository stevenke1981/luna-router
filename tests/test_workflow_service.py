"""Workflow adapter fixtures plus a real local verifier; no live model claims."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import luna_mcp
import router
import workflow_service as service
from workflow import WorkflowStore
from test_workflow import plan, task


def runtime():
    return {'source': 'codex.model/list', 'simulation': False, 'auth_mode': 'chatgpt',
            'models': [{'id': 'gpt-6.1-sol', 'efforts': ['low', 'medium', 'high'],
                        'input_modalities': ['text', 'image']} ]}


class WorkflowServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.state = self.root / 'state'
        self.config = router.read_json(router.ROOT / 'config.json')
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop('LUNA_ROUTER_WORKER', None)

    def call(self, operation, **args):
        return service.handle(operation, args, state_dir=self.state, config=self.config)

    def start(self):
        return self.call('start', project=str(self.project), tasks=[task()])['job']['id']

    def route(self, job):
        with patch.object(service, 'resolve_key', return_value=('fixture', 'process')), \
             patch.object(service.codex_backend, 'discover', return_value=runtime()), \
             patch.object(router, 'make_plan', return_value=plan()):
            return self.call('decide-online', job_id=job, task_id='a')

    def test_decision_returns_without_starting_a_worker(self):
        job = self.start()
        with patch.object(service.codex_backend, 'run_task') as worker:
            result = self.route(job)
        worker.assert_not_called()
        self.assertTrue(result['dispatch_ready'])
        self.assertFalse(result['worker_started'])
        descriptor = self.call('dispatch', job_id=job, task_id='a')
        self.assertEqual(descriptor['host_action'], 'spawn_agent')
        self.assertEqual(descriptor['reasoning_effort'], 'medium')
        self.assertEqual(descriptor['fork_turns'], 'none')

    def test_missing_key_keeps_task_retryable_without_worker(self):
        job = self.start()
        with patch.object(service, 'resolve_key', return_value=('', 'missing')):
            result = self.call('decide-online', job_id=job, task_id='a')
        self.assertEqual(result['continuation'], 'continue_independent_work')
        self.assertEqual(self.call('status', job_id=job)['tasks'][0]['status'], 'routing_blocked')
        self.assertTrue(self.route(job)['dispatch_ready'])

    def test_invalid_response_does_not_strand_task(self):
        job = self.start()
        with patch.object(service, 'resolve_key', return_value=('fixture', 'process')), \
             patch.object(service.codex_backend, 'discover', return_value=runtime()), \
             patch.object(router, 'make_plan', return_value={'status': 'planned'}):
            result = self.call('decide-online', job_id=job, task_id='a')
        self.assertFalse(result['dispatch_ready'])
        self.assertEqual(self.call('status', job_id=job)['tasks'][0]['status'], 'routing_blocked')

    def test_decision_returns_only_sanitized_store_plan(self):
        job = self.start()
        response = plan()
        response['private_extra'] = 'must-not-return'
        with patch.object(service, 'resolve_key', return_value=('fixture', 'process')), \
             patch.object(service.codex_backend, 'discover', return_value=runtime()), \
             patch.object(router, 'make_plan', return_value=response):
            result = self.call('decide-online', job_id=job, task_id='a')
        self.assertNotIn('private_extra', result['plan'])
        self.assertEqual(result['plan'], self.call('status', job_id=job)['tasks'][0]['plan'])

    def test_real_verifier_gate_after_native_fixture_events(self):
        job = self.start()
        self.route(job)
        self.call('dispatch', job_id=job, task_id='a')
        self.call('event', job_id=job, task_id='a', action='started', agent_id='fixture-a')
        self.call('event', job_id=job, task_id='a', action='progress', summary='Observed fixture action')
        self.call('event', job_id=job, task_id='a', action='completed', outcome='completed')
        with patch.object(service.codex_backend, 'discover', return_value=runtime()):
            descriptor = self.call('integrate', job_id=job)
        self.assertEqual((descriptor['model'], descriptor['effort']), ('gpt-6.1-sol', 'high'))
        self.call('event', job_id=job, action='integration_started', agent_id='fixture-integrator')
        self.call('event', job_id=job, action='integration_completed', outcome='completed')
        failed = self.call('verify', job_id=job, check_file=str(self.root / 'missing.json'))
        self.assertEqual(failed['status'], 'blocked')
        self.assertEqual(self.call('status', job_id=job)['job']['verification_attempt']['reason'],
                         'verifier_contract_invalid')
        check = self.root / 'check.json'
        check.write_text(json.dumps({'argv': [sys.executable, '-c', 'print("real verifier process")']}))
        result = self.call('verify', job_id=job, check_file=str(check))
        self.assertEqual(result['job']['status'], 'completed')
        self.assertEqual(result['job']['verification']['exit_code'], 0)
        self.assertEqual(len(result['job']['verification']['evidence_sha256']), 64)
        with patch.object(service, 'run_check') as check_again, self.assertRaises(ValueError):
            self.call('verify', job_id=job, check_file=str(check))
        check_again.assert_not_called()

    def test_no_verifier_execution_before_integration(self):
        job = self.start()
        with patch.object(service, 'run_check') as check, self.assertRaises(ValueError):
            self.call('verify', job_id=job, check_file='missing')
        check.assert_not_called()

    def test_request_guards_and_worker_marker(self):
        for args in [dict(project=str(self.project), tasks=[task()], config=[]),
                     dict(project=str(self.project), tasks=[task()], live=False)]:
            with self.assertRaises(ValueError):
                self.call('start', **args)
        with patch.dict(os.environ, {'LUNA_ROUTER_WORKER': '1'}), self.assertRaises(ValueError):
            self.start()

    def test_mcp_stdio_advertises_new_tools_without_execution(self):
        process = subprocess.run([sys.executable, str(router.ROOT / 'luna_mcp.py')],
            input=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}) + '\n',
            capture_output=True, text=True, encoding='utf-8', timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        names = {t['name'] for t in json.loads(process.stdout)['result']['tools']}
        self.assertTrue({'luna_decide', 'luna_workflow_start', 'luna_workflow_verify', 'luna_run'} <= names)

    def test_cli_status_returns_json_without_model_turn(self):
        job = self.start()
        request = self.root / 'request.json'
        request.write_text(json.dumps({'job_id': job}))
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = service.main(['status', '--request', str(request), '--state-dir', str(self.state)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['job']['id'], job)
