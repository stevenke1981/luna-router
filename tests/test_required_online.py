"""v2.7 tests use dummy keys, loopback HTTP and a fake Codex process only."""
from __future__ import annotations
import contextlib
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import MagicMock, patch
import credentials
import install_router
import luna
import luna_mcp
import router
import test_auto_cli_integration as cli_fixtures

ROOT=Path(__file__).resolve().parents[1]

@unittest.skipIf(os.name=='nt','Process fixture is Unix-only; not native Windows QA')
class RequiredOnlineHttpTests(unittest.TestCase):
    def setUp(self):
        cli_fixtures.AutoCliIntegration.setUp(self)
        self.env.pop("LUNA_ROUTER_WORKER", None)
        self.posts=[]
        self.status=200
        self.response_model=router.DECISION_MODEL
        self.low_confidence=False
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):
                pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.posts.append({'path':self.path,'body':body,'authorization':self.headers.get('Authorization')})
                if owner.status!=200:
                    value={'error':{'code':owner.status,'message':'fixture error'}}
                else:
                    answers={}
                    for name,question in body['questions'].items():
                        options=list(question['criteria'])
                        selected=('high' if 'high' in options else ('gpt-6.1-sol' if 'gpt-6.1-sol' in options else options[0]))
                        if owner.low_confidence and name=='effort':
                            probabilities={x:1/len(options) for x in options}
                        else:
                            probabilities={x:float(x==selected) for x in options}
                        answers[name]={'type':'choice','choice':selected,'probabilities':probabilities}
                    value={'id':'fixture-luna-'+str(len(owner.posts)), 'model':owner.response_model,
                           'answers':answers,'usage':{'cost':0.0001}}
                data=json.dumps(value).encode()
                self.send_response(owner.status)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        self.http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)
        self.env.update(ROUTER_FAKE_MODEL_LIST_OK='1',OPENROUTER_API_KEY='fixture-key-not-real')
        self.url='http://127.0.0.1:'+str(self.http.server_port)

    def call(self,*extra,command='run',env=None):
        report=self.root/('result-'+str(len(list(self.root.glob('result-*'))))+'.json')
        launcher=('import sys;sys.path.insert(0,'+repr(str(ROOT))+');'
                  'import openrouter_backend as b;b.BASE_URL='+repr(self.url)+';'
                  'import luna;sys.exit(luna.main(sys.argv[1:]))')
        argv=[sys.executable,'-c',launcher,command,'--prompt','修復 Rust 錯誤 PRIVATE_SENTINEL',
              '--project',str(self.root),'--state-dir',str(self.root/'state'),'--output',str(report),*extra]
        out=subprocess.run(argv,cwd=self.root,env=env or self.env,capture_output=True,text=True,timeout=15)
        result=json.loads(report.read_text()) if report.exists() else {}
        return out,result

    def test_default_run_posts_luna_then_launches_worker_even_single_candidate(self):
        out,result=self.call()
        self.assertEqual(out.returncode,0,out.stderr)
        self.assertEqual(len(self.posts),1)
        self.assertEqual(self.posts[0]['path'],'/api/alpha/decisions')
        self.assertEqual(self.posts[0]['authorization'],'Bearer fixture-key-not-real')
        self.assertNotIn('PRIVATE_SENTINEL',json.dumps(self.posts[0]['body']))
        self.assertEqual(result['plan']['decision_source'],'luna_decisions')
        self.assertEqual(result['plan']['decision_response_id'],'fixture-luna-1')
        self.assertTrue(result['worker_started'])
        self.assertTrue(result['worker_completed'])
        self.assertTrue((self.root/'submitted_task.txt').is_file())
        self.assertEqual(result['execution']['requested_effort'],'high')
        self.assertIn('luna_request_started',out.stderr)
        self.assertIn('worker_dispatch',out.stderr)
        self.assertNotIn('fixture-key-not-real',out.stdout+out.stderr)

    def test_two_new_runs_do_not_hide_behind_decision_cache(self):
        for _ in range(2):
            out,result=self.call()
            self.assertEqual(out.returncode,0,out.stderr)
            self.assertTrue(result['plan']['decision_model_called'])
        self.assertEqual(len(self.posts),2)

    def test_missing_key_is_error_not_offline_worker(self):
        out,result=self.call(env={**self.env,'OPENROUTER_API_KEY':''})
        self.assertEqual(out.returncode,2)
        self.assertEqual(result['message'],'openrouter_key_missing_online_required')
        self.assertFalse(result['worker_started'])
        self.assertEqual(self.posts,[])
        self.assertFalse((self.root/'submitted_task.txt').exists())

    def test_401_402_404_429_503_never_run_local_worker(self):
        for code in (401,402,404,429,503):
            with self.subTest(code=code):
                self.status=code
                out,result=self.call()
                self.assertEqual(out.returncode,2,out.stderr)
                self.assertEqual(result['plan']['decision_source'],'luna_decisions_error')
                self.assertIn({'http_status':code,'retry_after':None},result['plan']['notes'])
                self.assertFalse(result['worker_started'])
        self.assertEqual(len(self.posts),5)
        self.assertFalse((self.root/'submitted_task.txt').exists())

    def test_low_certainty_luna_choice_dispatches_without_review(self):
        self.low_confidence=True
        out,result=self.call()
        self.assertEqual(out.returncode,0,out.stderr)
        self.assertEqual(result['plan']['decision_source'],'luna_decisions')
        self.assertIn('luna_low_certainty_accepted',result['plan']['notes'])
        self.assertTrue(result['plan']['decision_model_called'])
        self.assertTrue(result['worker_started'])

    def test_wrong_model_response_never_counts_as_luna_success(self):
        self.response_model='other/decision-model'
        out,result=self.call()
        self.assertEqual(out.returncode,2,out.stderr)
        self.assertFalse(result['plan']['decision_model_called'])
        self.assertFalse(result['worker_started'])

    def test_plan_calls_luna_but_does_not_execute(self):
        out,result=self.call(command='plan')
        self.assertEqual(out.returncode,0,out.stderr)
        self.assertTrue(result['plan']['decision_model_called'])
        self.assertFalse(result['worker_started'])
        self.assertFalse((self.root/'submitted_task.txt').exists())

    def test_explicit_offline_is_respected(self):
        out,result=self.call('--offline')
        self.assertEqual(out.returncode,0,out.stderr)
        self.assertFalse(result['plan']['decision_model_called'])
        self.assertTrue(result['worker_started'])
        self.assertEqual(self.posts,[])

    def test_auto_alias_also_requires_online(self):
        out,result=self.call('--routing','auto')
        self.assertEqual(out.returncode,0,out.stderr)
        self.assertTrue(result['plan']['decision_model_called'])
        self.assertEqual(len(self.posts),1)

    def test_no_external_does_not_silently_become_local(self):
        out,result=self.call('--data-policy','no_external')
        self.assertEqual(out.returncode,2,out.stderr)
        self.assertIn('no_external_conflicts',result['message'])
        self.assertEqual(self.posts,[])

    def test_discovery_error_is_capability_block_not_fake_luna(self):
        out,result=self.call(env={**self.env,'ROUTER_FAKE_MODEL_LIST_OK':'0'})
        self.assertEqual(out.returncode,2,out.stderr)
        self.assertEqual(result['plan']['reason'],'no_verified_worker_candidates')
        self.assertFalse(result['worker_started'])

    def test_probe_makes_one_real_http_request_without_codex(self):
        with patch.object(router.orb,'BASE_URL',self.url),patch.dict(os.environ,self.env),contextlib.redirect_stdout(io.StringIO()):
            code=luna.probe(self.root/'probe.json')
        self.assertEqual(code,0)
        self.assertEqual(len(self.posts),1)
        data=json.loads((self.root/'probe.json').read_text())
        self.assertTrue(data['decision_model_called'])
        self.assertFalse(data['worker_started'])

    def test_pinned_worker_still_uses_luna_for_effort(self):
        task=self.root/'pinned-task.json'
        task.write_text(json.dumps({'objective':'Fix code','kind':'coding','requested_model':'gpt-6.1-sol'}))
        with patch.object(router.orb,'BASE_URL',self.url),patch.dict(os.environ,self.env),contextlib.redirect_stdout(io.StringIO()):
            code=router.main(['run','--task',str(task),'--project',str(self.root),
                              '--state-dir',str(self.root/'state')])
        self.assertEqual(code,0)
        self.assertEqual(len(self.posts),1)

    def test_retry_same_effort_failures_do_not_repeat_luna(self):
        verifier=self.root/'verify.json'
        verifier.write_text(json.dumps({'argv':[sys.executable,'-c','raise SystemExit(1)']}))
        out,result=self.call('--job-id','retry-test','--verify-argv-file',str(verifier),
                             '--auto-retry','--max-attempts','3')
        self.assertEqual(out.returncode,3,out.stderr)
        self.assertEqual(len(self.posts),1)
        self.assertEqual([x['effort'] for x in result['attempt_history']],['high','high','xhigh'])
        self.assertEqual(result['retry']['failures'],1)
        out,result=self.call('--job-id','retry-test','--verify-argv-file',str(verifier))
        self.assertEqual(out.returncode,3,out.stderr)
        self.assertEqual(len(self.posts),1)


class EntryAndCredentialTests(unittest.TestCase):
    def setUp(self):
        # These entry fixtures represent a host caller; the explicit recursion test
        # below sets the worker marker again and still exercises the real guard.
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("LUNA_ROUTER_WORKER", None)

    def test_entrypoint_with_no_subcommand_defaults_to_run(self):
        with patch.object(router,'main',return_value=0) as call:
            self.assertEqual(luna.main(['--prompt','task']),0)
        self.assertEqual(call.call_args.args[0][0],'run')

    def test_old_config_cannot_choose_offline_implicitly(self):
        args=types.SimpleNamespace(routing=None,offline=False,online=False)
        self.assertEqual(router.routing_mode(args,{'routing':{'default_mode':'local'}}),'luna')

    def test_process_key_takes_precedence(self):
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'fixture-value'}):
            self.assertEqual(credentials.resolve_key(),('fixture-value','process'))
            self.assertNotIn('fixture-value',json.dumps(credentials.key_status()))

    def test_windows_machine_environment_fallback(self):
        fake=MagicMock()
        fake.HKEY_CURRENT_USER='user';fake.HKEY_LOCAL_MACHINE='machine'
        fake.KEY_READ=1;fake.REG_SZ=1;fake.REG_EXPAND_SZ=2
        def openkey(hive,*args):
            if hive=='user':
                raise FileNotFoundError()
            handle=MagicMock()
            handle.__enter__.return_value='handle'
            return handle
        fake.OpenKey.side_effect=openkey
        fake.QueryValueEx.return_value=('machine-fixture',1)
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':''}),patch('credentials.os.name','nt'),patch.dict(sys.modules,{'winreg':fake}):
            self.assertEqual(credentials.resolve_key(),('machine-fixture','system_environment'))
        fake.SetValueEx.assert_not_called()

    def test_mcp_schema_defaults_to_live_true(self):
        for tool in luna_mcp.TOOLS[:2]:
            self.assertIs(tool['inputSchema']['properties']['live']['default'],True)

    def test_mcp_run_omitted_live_uses_default_online_main(self):
        def engine(argv):
            target=argv[argv.index('--output')+1]
            router.save_json(target,{'status':'fixture'})
            self.assertEqual(argv[0],'run')
            self.assertNotIn('--offline',argv)
            return 0
        with patch.object(router,'main',side_effect=engine):
            code,result=luna_mcp.invoke('luna_run',{'task':{'objective':'Test'},'project':str(ROOT)})
        self.assertEqual(code,0)

    def test_mcp_explicit_live_false_only_maps_to_explicit_offline(self):
        def engine(argv):
            router.save_json(argv[argv.index('--output')+1],{'status':'fixture'})
            self.assertIn('--offline',argv)
            self.assertEqual(argv[0],'plan')
            return 0
        with patch.object(router,'main',side_effect=engine):
            luna_mcp.invoke('luna_plan',{'task':{'objective':'Test'},'project':str(ROOT),'live':False})

    def test_mcp_string_false_is_rejected(self):
        with self.assertRaises(ValueError):
            luna_mcp.invoke('luna_run',{'task':{},'project':str(ROOT),'live':'false'})

    def test_worker_cannot_recursively_route(self):
        with patch.dict(os.environ,{'LUNA_ROUTER_WORKER':'1'}),self.assertRaises(ValueError):
            luna_mcp.invoke('luna_run',{})

    def test_mcp_stdio_initializes_and_exposes_tools(self):
        messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25'}},
                  {'jsonrpc':'2.0','method':'notifications/initialized'},
                  {'jsonrpc':'2.0','id':2,'method':'tools/list'}]
        out=subprocess.run([sys.executable,str(ROOT/'luna_mcp.py')],input='\n'.join(map(json.dumps,messages))+'\n',
                           text=True,capture_output=True,timeout=10)
        self.assertEqual(out.returncode,0,out.stderr)
        replies=[json.loads(line) for line in out.stdout.splitlines()]
        self.assertEqual(len(replies),2)
        self.assertEqual(replies[0]['result']['serverInfo']['version'],'2.7.0')
        self.assertEqual(replies[1]['result']['tools'][0]['name'],'luna_run')


class McpWireEncodingTests(unittest.TestCase):
    def test_non_ascii_result_is_ascii_json_and_utf8_input_is_read(self):
        received = []
        def dispatch(message):
            received.append(message['params']['name'])
            return {'jsonrpc':'2.0','id':message['id'],'result':{'text':'目前被執行環境阻擋 — NOT_RUN'}}
        request = json.dumps({'jsonrpc':'2.0','id':7,'method':'tools/call',
                              'params':{'name':'修復'}}, ensure_ascii=False) + '\n'
        stdin = io.TextIOWrapper(io.BytesIO(request.encode('utf-8')), encoding='cp950', errors='strict')
        raw = io.BytesIO()
        stdout = io.TextIOWrapper(raw, encoding='cp950')
        stderr = io.TextIOWrapper(io.BytesIO(), encoding='cp950')
        with patch.object(luna_mcp, 'dispatch', side_effect=dispatch), \
             patch.object(sys, 'stdin', stdin), patch.object(sys, 'stdout', stdout), patch.object(sys, 'stderr', stderr):
            luna_mcp.main()
            stdout.flush()
        self.assertEqual(received, ['修復'])
        line = raw.getvalue()
        self.assertTrue(line.isascii())
        self.assertEqual(json.loads(line)['result']['text'], '目前被執行環境阻擋 — NOT_RUN')


class FullInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source'
        def ignore_fixture_tree(directory, names):
            ignored = set(shutil.ignore_patterns('.state', '.git', '__pycache__', '*.pyc')(directory, names))
            # TEMP may live anywhere under ROOT. Exclude the ancestor containing
            # this fixture, rather than relying on a particular temp folder name.
            for name in names:
                candidate = (Path(directory) / name).resolve()
                if candidate == self.root.resolve() or candidate in self.root.resolve().parents:
                    ignored.add(name)
            return ignored
        shutil.copytree(ROOT,self.source,ignore=ignore_fixture_tree)
        lines=[]
        for file in sorted(self.source.rglob('*')):
            if file.is_file() and file.name!='MANIFEST.sha256':
                lines.append(hashlib.sha256(file.read_bytes()).hexdigest()+'  '+file.relative_to(self.source).as_posix())
        (self.source/'MANIFEST.sha256').write_text('\n'.join(lines)+'\n')
        self.home=self.root/'user'/'codex-home'
        self.home.mkdir(parents=True)
        self.original='model = "keep-my-model"\n[unrelated]\nsetting = true\n'
        (self.home/'config.toml').write_text(self.original)

    def test_installs_full_runtime_and_absolute_skill_cross_directory(self):
        result=install_router.install(self.source,self.home)
        runtime=Path(result['runtime'])
        self.assertTrue((runtime/'credentials.py').is_file())
        skill=Path(result['skill']).read_text(encoding='utf-8')
        self.assertNotIn('{{ROUTER_PATH}}',skill)
        self.assertIn(str(runtime/'luna.py'),skill)
        self.assertEqual((self.home/'config.toml').read_text(),self.original)
        process=subprocess.run([sys.executable,str(runtime/'luna.py'),'--prompt','Test',
                                '--project',str(self.root)],env={**{k: v for k, v in os.environ.items() if k != 'LUNA_ROUTER_WORKER'},
                                     'OPENROUTER_API_KEY':'','LUNA_ROUTER_IGNORE_REGISTRY_KEY':'1'},
                                cwd=self.root,text=True,capture_output=True,timeout=10)
        self.assertEqual(process.returncode,2)
        self.assertIn('openrouter_key_missing_online_required',process.stderr)
        self.assertNotIn('ModuleNotFoundError',process.stderr)

    def test_mcp_registration_is_idempotent_preserves_unrelated_model(self):
        one=install_router.install(self.source,self.home,True)
        two=install_router.install(self.source,self.home,True)
        data=(self.home/'config.toml').read_text()
        self.assertIn(self.original.strip(),data)
        self.assertEqual(data.count(install_router.BEGIN),1)
        self.assertTrue((Path(two['backup'])/'runtime'/'router.py').is_file())
        import tomllib
        settings=tomllib.loads(data)
        self.assertEqual(settings['model'],'keep-my-model')
        self.assertEqual(settings['mcp_servers'][install_router.SERVER]['tool_timeout_sec'],1900)
        self.assertNotIn('OPENROUTER_API_KEY =',data)

    def test_modified_release_is_rejected_before_install(self):
        (self.source/'router.py').write_text('print("altered")')
        with self.assertRaises(ValueError):
            install_router.install(self.source,self.home)
        self.assertFalse((self.home/'luna-router').exists())

    def test_unmanaged_mcp_conflict_is_not_overwritten(self):
        original=self.original+'\n[mcp_servers.'+install_router.SERVER+']\ncommand="other"\n'
        (self.home/'config.toml').write_text(original)
        with self.assertRaises(ValueError):
            install_router.install(self.source,self.home,True)
        self.assertEqual((self.home/'config.toml').read_text(),original)
