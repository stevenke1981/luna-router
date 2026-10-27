"""FIXTURE tests only: fake subprocess, no API or live worker execution."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('live_smoke', Path(__file__).resolve().parents[1] / 'examples/live-smoke.py')
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class LiveSmokeFixtureTests(unittest.TestCase):
    def exercise(self, mutation=None, artifact='correct', returncode=0):
        def fake(argv, **kwargs):
            self.assertEqual(argv[2], 'run')
            self.assertIn('metadata_only', argv)
            self.assertNotIn('--offline', argv)
            self.assertEqual(kwargs['encoding'], 'utf-8')
            project = Path(argv[argv.index('--project') + 1])
            data = json.loads((project / 'input.json').read_text())
            receipt = {'plan': {'status': 'planned', 'simulation': False,
                               'decision_call_attempted': True, 'decision_response_received': True,
                               'decision_source': 'luna_decisions', 'decision_model_called': True,
                               'decision_response_id': 'fixture-decision'},
                       'worker_started': True, 'worker_completed': True,
                       'execution': {'worker_started': True, 'status': 'turn_completed', 'exit_code': 0}}
            if mutation:
                mutation(receipt)
            Path(argv[argv.index('--output') + 1]).write_text(json.dumps(receipt))
            if artifact != 'missing':
                value = {'answer': sum(data['numbers']), 'nonce': data['nonce']}
                if artifact == 'stale': value['nonce'] = 'old-fixture-nonce'
                if artifact == 'wrong': value['answer'] += 1
                (project / 'result.json').write_text(json.dumps(value), encoding='utf-8-sig' if artifact == 'bom' else 'utf-8')
            return subprocess.CompletedProcess(argv, returncode, '', '')
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ):
            os.environ.pop('LUNA_ROUTER_WORKER', None)
            result = smoke.run_smoke(folder, runner=fake)
            self.assertTrue((Path(folder) / 'receipt.json').exists())
            return result

    def test_fixture_success_requires_all_four_layers(self):
        result = self.exercise()
        self.assertTrue(result['passed'])
        self.assertEqual(len(result['layers']), 4)

    def test_fixture_missing_stale_or_wrong_artifact_fails(self):
        for artifact in ('missing', 'stale', 'wrong'):
            with self.subTest(artifact=artifact):
                self.assertFalse(self.exercise(artifact=artifact)['passed'])

    def test_powershell_bom_artifact_is_supported(self):
        self.assertTrue(self.exercise(artifact='bom')['passed'])

    def test_fixture_receipt_cannot_replace_acceptance(self):
        mutations = [lambda d: d['plan'].update(decision_source='local_policy'),
                     lambda d: d['plan'].update(decision_response_id=None),
                     lambda d: d.update(worker_started=False),
                     lambda d: d['execution'].update(status='failed'),
                     lambda d: d['execution'].update(exit_code=1),
                     lambda d: d['plan'].update(simulation=True),
                     lambda d: d['plan'].update(status='blocked'),
                     lambda d: d['plan'].update(decision_response_received=False),
                     lambda d: d.update(plan=[]),
                     lambda d: d.update(execution=None),
                     lambda d: d.update(status='blocked')]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assertFalse(self.exercise(mutation)['passed'])
        self.assertFalse(self.exercise(returncode=2)['passed'])

    def test_nonempty_evidence_directory_is_preserved_and_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            artifact = Path(folder) / 'result.json'
            artifact.write_text('existing evidence')
            result = smoke.run_smoke(folder, runner=lambda *a, **k: self.fail('stale run'))
            self.assertFalse(result['passed'])
            self.assertEqual(artifact.read_text(), 'existing evidence')

    def test_fixture_worker_marker_never_launches_router(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'LUNA_ROUTER_WORKER': '1'}):
            result = smoke.run_smoke(folder, runner=lambda *a, **k: self.fail('recursive launch'))
            self.assertFalse(result['passed'])
