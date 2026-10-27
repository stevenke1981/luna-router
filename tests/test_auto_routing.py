"""Auto Routing v2.6 regression tests. No real API calls or Codex inference."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import router
import codex_backend as cb
from policy import automatic_metadata_state, normalize_task, routing_state


class AutoRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        self.config = router.read_json(router.ROOT / 'config.json')
        self.runtime = router.read_json(router.ROOT / 'examples/runtime-demo.json')
        self.runtime['simulation'] = False
        self.text = router.read_json(router.ROOT / 'evidence/openrouter-text-free.json')
        self.decisions = router.read_json(router.ROOT / 'evidence/openrouter-decisions-luna.json')

    def task(self, **updates):
        return normalize_task({"objective": "Fix secret-project SYNC123 in a Rust app", "kind": "debugging",
                               "complexity": 2, "needs_tools": True, **updates})

    def test_default_mode_is_auto_and_flags_override_it(self):
        defaults = argparse.Namespace(online=False, offline=False, routing=None)
        self.assertEqual(router.routing_mode(defaults, self.config), 'luna')
        defaults.offline = True
        self.assertEqual(router.routing_mode(defaults, self.config), 'local')
        defaults.offline = False
        defaults.online = True
        self.assertEqual(router.routing_mode(defaults, self.config), 'luna')

    def test_auto_metadata_never_contains_task_or_summary(self):
        task = self.task(summary='Do not send API keys XYZ-ABC', summary_approved=False)
        self.assertIsNone(routing_state(task))
        state = automatic_metadata_state(task)
        data = json.dumps(state)
        self.assertNotIn('SYNC123', data)
        self.assertNotIn('XYZ-ABC', data)
        self.assertNotIn('secret-project', data)
        self.assertFalse(state.get('summary_approved', False))
        self.assertEqual(state['kind'], 'debugging')

    def test_no_external_never_creates_metadata(self):
        self.assertIsNone(automatic_metadata_state(self.task(data_policy='no_external')))

    def test_auto_uses_remote_decider_without_summary_approval_but_only_metadata(self):
        task = self.task()
        calls = []
        def remote(body, *args):
            calls.append(body)
            criteria = list(body['questions']['route']['criteria'])
            effort = list(body['questions']['effort']['criteria'])
            def choice(keys, picked):
                return {'type':'choice','choice':picked,
                        'probabilities':{v: (1.0 if v==picked else 0.0) for v in keys}}
            return {'model':'openai/gpt-6-luna-decisions','answers': {
                'route':choice(criteria, 'gpt-6.1-sol'),
                'effort':choice(effort,'high')}}
        ledger = MagicMock()
        ledger.get.return_value = None
        with patch.object(router.orb, 'decide', side_effect=remote):
            plan = router.make_plan(task,self.config,self.runtime,self.text,self.decisions,
                                    online=True,ledger=ledger,job='t',auto_metadata=True)
        self.assertEqual(plan['decision_source'], 'luna_decisions')
        self.assertTrue(plan['decision_model_called'])
        self.assertEqual(plan['selected_model'],'gpt-6.1-sol')
        outbound = json.dumps(calls,ensure_ascii=False)
        self.assertNotIn('SYNC123',outbound)
        self.assertNotIn('secret-project',outbound)
        self.assertIn('Task classification metadata only',outbound)

    def test_auto_without_key_does_not_request_remote_inference(self):
        with patch.dict(os.environ, {'OPENROUTER_API_KEY':'','LUNA_ROUTER_IGNORE_REGISTRY_KEY':'1'}), patch.object(router.orb,'decide') as decider:
            self.assertFalse(router.auto_decision_enabled('auto',self.task(),self.config))
            plan = router.make_plan(self.task(),self.config,self.runtime,self.text,self.decisions,
                                    online=False,auto_metadata=True)
        self.assertEqual(plan['decision_source'], 'local_policy')
        decider.assert_not_called()

    def test_explicit_model_pin_skips_decisions(self):
        task=self.task(requested_model='gpt-6.1-sol')
        with patch.object(router.orb,'decide') as decider:
            plan=router.make_plan(task,self.config,self.runtime,self.text,self.decisions,
                                  online=True,ledger=MagicMock(),job='t',auto_metadata=True)
        self.assertEqual(plan['decision_source'],'explicit_model')
        decider.assert_not_called()

    def test_safe_default_fallback_does_not_pretend_to_switch_model(self):
        runtime={'models':[],'simulation':False,'discovery_failed':True,
                 'discovery_error':'app_server_timeout','auth_mode':'chatgpt'}
        plan=router.make_plan(self.task(),self.config,runtime,{'data':[]},{'data':[]},
                              online=True,auto_metadata=True)
        self.assertEqual(plan['status'],'planned')
        self.assertIsNone(plan['selected_model'])
        self.assertIsNone(plan['requested_effort'])
        self.assertEqual(plan['backend'],'codex_default')
        self.assertFalse(plan['decision_model_called'])
        args=argparse.Namespace(project=str(self.project),sandbox='read-only')
        with patch.object(router.codex_backend,'run_default_task',return_value={
            'status':'turn_completed','actual_model':None,'actual_effort':None,
        }) as run:
            execution=router.execute_plan(plan,self.task(),runtime,args,ledger=None,job='x')
        self.assertEqual(execution['status'],'turn_completed')
        run.assert_called_once()
        # The worker is told it is the worker, so it never re-enters the router.
        self.assertTrue(run.call_args.args[0].startswith(router.WORKER_HEADER))
        self.assertTrue(run.call_args.args[0].endswith(self.task()['objective']))

    def test_high_risk_and_explicit_pin_disallow_unspecified_default(self):
        runtime={'models':[],'simulation':False,'discovery_failed':True,'auth_mode':'chatgpt'}
        for updates in ({'risk':'high'},{'complexity':4},{'requested_model':'gpt-6-astra'}):
            with self.subTest(updates=updates):
                p=router.make_plan(self.task(**updates),self.config,runtime,{'data':[]},{'data':[]})
                self.assertEqual(p['status'],'blocked')

    def test_unknown_login_does_not_run_default(self):
        runtime={'models':[],'simulation':False,'discovery_failed':True,'auth_mode':'unknown'}
        p=router.make_plan(self.task(),self.config,runtime,{'data':[]},{'data':[]})
        self.assertEqual(p['status'],'blocked')

    def test_collect_surfaces_sanitized_model_list_error_and_login(self):
        args=argparse.Namespace(demo=False,command='run',_routing_mode='auto',runtime=None,catalog_file=None)
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'','LUNA_ROUTER_IGNORE_REGISTRY_KEY':'1'}), \
             patch.object(router.shutil,'which',return_value='codex'), \
             patch.object(router.codex_backend,'discover',side_effect=cb.CodexBackendError('app_server_timeout')), \
             patch.object(router.codex_backend,'login_status',return_value={'auth_mode':'chatgpt'}), \
             patch.object(router.orb,'catalog') as net:
            runtime,text,decision,notes=router.collect(args,self.task())
        self.assertTrue(runtime['discovery_failed'])
        self.assertEqual(runtime['auth_mode'],'chatgpt')
        self.assertIn({'codex_discovery_error':'app_server_timeout'},notes)
        net.assert_not_called()

    def _main(self, task_json=None, *flags):
        output=self.project / ('main-result-'+str(len(list(self.project.glob('main-result-*'))))+'.json')
        args=['run','--project',str(self.project),'--output',str(output)]
        if task_json is None:
            args += ['--prompt','修復 Rust 專案的錯誤']
        else:
            task_path=self.project / 'task.json'
            task_path.write_text(json.dumps(task_json),encoding='utf-8')
            args+=['--task',str(task_path)]
        args += list(flags)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code=router.main(args)
        return code,json.loads(output.read_text(encoding='utf-8'))

    def test_run_auto_without_online_flag_starts_codex_worker(self):
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'','LUNA_ROUTER_IGNORE_REGISTRY_KEY':'1'}), \
             patch.object(router,'collect',return_value=(self.runtime,self.text,self.decisions,[])), \
             patch.object(router.codex_backend,'run_task',return_value={'status':'turn_completed','actual_model':None}) as worker, \
             patch.object(router.orb,'decide') as decider:
            code,result=self._main(None,'--offline')
        self.assertEqual(code,0)
        self.assertEqual(result['routing_mode'],'local')
        self.assertEqual(result['plan']['selected_model'],'gpt-6.1-sol')
        self.assertEqual(result['execution']['status'],'turn_completed')
        self.assertEqual(result['acceptance'],'not_independently_verified')
        self.assertFalse(result['model_switch_verified'])
        worker.assert_called_once()
        decider.assert_not_called()

    def test_run_auto_with_key_invokes_luna_without_online_flag(self):
        def respond(body, *args):
            keys=list(body['questions']['route']['criteria'])
            efforts=list(body['questions']['effort']['criteria'])
            def answer(options,pick):
                return {'type':'choice','choice':pick,
                        'probabilities':{x:1.0 if x==pick else 0.0 for x in options}}
            return {'model':'openai/gpt-6-luna-decisions',
                    'answers':{'route':answer(keys,'gpt-6.1-sol'),
                               'effort':answer(efforts,'high')}}
        fake_ledger=MagicMock()
        fake_ledger.get.return_value=None
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'test-key'}), \
             patch.object(router,'collect',return_value=(self.runtime,self.text,self.decisions,[])), \
             patch.object(router,'Ledger',return_value=fake_ledger), \
             patch.object(router.codex_backend,'run_task',return_value={'status':'turn_completed'}), \
             patch.object(router.orb,'decide',side_effect=respond) as decision:
            code,result=self._main()
        self.assertEqual(code,0)
        self.assertEqual(result['routing_mode'],'luna')
        self.assertEqual(result['plan']['decision_source'],'luna_decisions')
        self.assertEqual(result['plan']['requested_effort'],'high')
        self.assertTrue(result['plan']['decision_model_called'])
        decision.assert_called_once()

    def test_public_text_only_can_route_to_verified_free_worker(self):
        task={'objective':'Summarize this public article', 'kind':'summary',
              'complexity':1,'risk':'low','needs_tools':False,'data_policy':'public'}
        selected=router.make_plan(normalize_task(task),self.config,self.runtime,self.text,{'data':[]})
        self.assertEqual(selected['backend'],'openrouter')
        with patch.object(router.orb,'catalog',return_value=self.text), \
             patch.object(router.orb,'complete',return_value={'status':'response_received'}) as free:
            result=router.execute_plan(selected,normalize_task(task),self.runtime,
                                       argparse.Namespace(project=str(self.project),sandbox='read-only'),
                                       ledger=MagicMock(),job='free-job')
        self.assertEqual(result['status'],'response_received')
        free.assert_called_once()

    def test_run_offline_flag_forbids_luna_even_with_key(self):
        with patch.dict(os.environ, {'OPENROUTER_API_KEY':'test-key'}), \
             patch.object(router,'collect',return_value=(self.runtime,self.text,self.decisions,[])), \
             patch.object(router.codex_backend,'run_task',return_value={'status':'turn_completed'}), \
             patch.object(router.orb,'decide') as decider:
            code,result=self._main(None,'--offline')
        self.assertEqual(code,0)
        self.assertEqual(result['routing_mode'],'local')
        decider.assert_not_called()

    def test_inline_prompt_is_classified_locally_without_luna_call(self):
        args=argparse.Namespace(task=None,prompt='跨系統 架構 設計',prompt_file=None,stdin=False,
                                kind=None,complexity=None,risk=None,text_only=False)
        result=router._inline_task(args)
        self.assertEqual(result['kind'],'architecture')
        self.assertEqual(result['complexity'],3)
        self.assertFalse(result['summary_approved'])
        self.assertEqual(result['data_policy'],'metadata_only')

    def test_no_external_task_with_key_does_not_call_decision(self):
        task=self.task(data_policy='no_external')
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'test-key'}), \
             patch.object(router.orb,'decide') as decider:
            self.assertFalse(router.auto_decision_enabled('auto',task,self.config))
            plan=router.make_plan(task,self.config,self.runtime,self.text,self.decisions,
                                  online=False,auto_metadata=True)
        self.assertEqual(plan['decision_source'],'local_policy')
        decider.assert_not_called()

    def low_certainty_plan(self, config, effort='medium'):
        def remote(body, *args):
            route = list(body['questions']['route']['criteria'])
            efforts = list(body['questions']['effort']['criteria'])
            picked_effort = effort
            def choice(keys, picked):
                # Chosen option leads but stays far below the 0.95/0.80 gate.
                top = 0.6 if len(keys) > 1 else 1.0
                rest = (1.0 - top) / (len(keys) - 1) if len(keys) > 1 else 0.0
                return {'type':'choice','choice':picked,
                        'probabilities':{v: top if v==picked else rest for v in keys}}
            return {'model':'openai/gpt-6-luna-decisions','id':'r1','answers':{
                'route':choice(route,'gpt-6.1-sol'),'effort':choice(efforts,picked_effort)}}
        ledger = MagicMock()
        ledger.get.return_value = None
        with patch.object(router.orb, 'decide', side_effect=remote):
            return router.make_plan(self.task(),config,self.runtime,self.text,self.decisions,
                                    online=True,ledger=ledger,job='t',auto_metadata=True,
                                    require_luna=True,fresh_decision=True)

    def test_low_certainty_luna_choice_is_used_immediately(self):
        plan = self.low_certainty_plan(self.config)
        self.assertEqual(plan['status'],'planned')
        self.assertEqual(plan['decision_source'],'luna_decisions')
        self.assertEqual(plan['selected_model'],'gpt-6.1-sol')
        self.assertEqual(plan['requested_effort'],'medium')
        self.assertIn('luna_low_certainty_accepted',plan['notes'])

    def test_low_certainty_gate_remains_available_as_opt_in(self):
        config = json.loads(json.dumps(self.config))
        config['policy']['block_low_certainty'] = True
        plan = self.low_certainty_plan(config)
        self.assertEqual(plan['status'],'blocked')
        self.assertEqual(plan['reason'],'luna_decision_below_threshold')


    def test_luna_effort_below_complexity_floor_is_kept(self):
        # complexity 2 has a local floor of medium; Luna's low must win.
        plan = self.low_certainty_plan(self.config, effort='low')
        self.assertEqual(plan['status'],'planned')
        self.assertEqual(plan['requested_effort'],'low')
        self.assertEqual(plan['reason'],'accepted')

    def test_local_effort_floor_remains_available_as_opt_in(self):
        config = json.loads(json.dumps(self.config))
        config['policy']['local_effort_floor'] = True
        plan = self.low_certainty_plan(config, effort='low')
        self.assertEqual(plan['requested_effort'],'medium')

    def test_unsupported_luna_effort_moves_to_nearest_supported_level(self):
        from policy import enforce_selection
        candidate = {'id':'m','model_id':'m','backend':'codex','family':'sol',
                     'description':'d','efforts':['medium','high'],'default_effort':'medium','priority':1}
        task = self.task(complexity=1)
        self.assertEqual(enforce_selection(task,[candidate],'m','low',effort_floor=False)['effort'],'medium')
        self.assertEqual(enforce_selection(task,[candidate],'m','max',effort_floor=False)['effort'],'high')


if __name__=='__main__':
    unittest.main()
