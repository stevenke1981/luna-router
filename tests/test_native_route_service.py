"""Native host-reported routing contract, with no external inference."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import router
import workflow_mcp
import workflow_service as service
from test_workflow import task


def capabilities():
    return {'source': 'codex.model/list', 'simulation': False, 'auth_mode': 'chatgpt',
            'models': [{'id': 'gpt-6-luna', 'efforts': ['medium']},
                       {'id': 'gpt-6.1-sol', 'efforts': ['low', 'medium', 'high', 'xhigh', 'max']},
                       {'id': 'gpt-6.1-terra', 'efforts': ['low', 'medium', 'high', 'xhigh', 'max']}]}


class NativeServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = router.read_json(router.ROOT / 'config.json')
        self.state = self.root / 'state'
        self.job = self.call('start', project=str(self.root), tasks=[task()])['job']['id']
        self.args = {'job_id': self.job, 'task_id': 'a', 'coordinator_agent_id': 'native-coordinator',
                     'selected_model': 'gpt-6.1-terra', 'requested_effort': 'max'}

    def call(self, action, **args):
        return service.handle(action, args, state_dir=self.state, config=self.config)

    def status(self):
        return self.call('status', job_id=self.job)['tasks'][0]['status']

    def test_default_native_selection_no_openrouter(self):
        with patch.object(service.codex_backend, 'discover', return_value=capabilities()), \
             patch.object(service, 'resolve_key', side_effect=AssertionError('key accessed')), \
             patch.object(router, 'make_plan', side_effect=AssertionError('network decision called')):
            result = self.call('decide', **self.args)
        self.assertTrue(result['dispatch_ready'])
        self.assertEqual(result['plan']['decision_source'], 'native_coordinator_host_reported')
        self.assertEqual(result['plan']['coordinator']['provenance'], 'host_reported')
        self.assertNotIn('decision_attempts', result)
        self.assertEqual(self.call('dispatch', job_id=self.job, task_id='a')['model'], 'gpt-6.1-terra')

    def test_invalid_input_never_strands_task(self):
        for overrides in ({'selected_model': 'gpt-6-astra'}, {'requested_effort': 'ultra'},
                          {'coordinator_agent_id': ''}):
            with self.assertRaises(ValueError):
                self.call('decide', **(self.args | overrides))
            self.assertEqual(self.status(), 'queued')
        with self.assertRaises(ValueError):
            self.call('decide', job_id=self.job, task_id='a')
        self.assertEqual(self.status(), 'queued')

    def test_capability_failures_never_strand_task(self):
        for runtime in ({**capabilities(), 'source': 'fixture'},
                        {**capabilities(), 'models': capabilities()['models'][:1]},
                        {**capabilities(), 'models': capabilities()['models'][1:]}):
            with patch.object(service.codex_backend, 'discover', return_value=runtime), self.assertRaises(ValueError):
                self.call('decide', **self.args)
            self.assertEqual(self.status(), 'queued')

    def test_wrong_coordinator_configuration_is_rejected(self):
        job = self.call('start', project=str(self.root), tasks=[task()],
                        config={'coordinator_effort': 'low'})['job']['id']
        with patch.object(service.codex_backend, 'discover') as discover, self.assertRaises(ValueError):
            self.call('decide', **(self.args | {'job_id': job}))
        discover.assert_not_called()
        self.assertEqual(self.call('status', job_id=job)['tasks'][0]['status'], 'queued')

    def test_mcp_schema_and_description(self):
        tool = next(t for t in workflow_mcp.TOOLS if t['name'] == 'luna_decide')
        self.assertEqual(set(tool['inputSchema']['required']), set(self.args))
        self.assertIn('does not run inference', tool['description'].lower())
        self.assertEqual(workflow_mcp.ACTIONS['luna_decide_online'], 'decide-online')
