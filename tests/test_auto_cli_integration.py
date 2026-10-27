"""Process-level integration against a FAKE local Codex CLI, no inference."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipIf(os.name=='nt', 'This subprocess fake uses a Unix shebang; Windows needs its own native QA')
class AutoCliIntegration(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.bin=self.root/'bin'
        self.bin.mkdir()
        fake=self.bin/'codex'
        fake.write_text(f'''#!{sys.executable}
import json,sys,os
from pathlib import Path
args=sys.argv[1:]
if args[:2]==['login','status']:
    print('Logged in using ChatGPT')
    sys.exit(0)
if args and args[0]=='app-server':
    if os.environ.get('ROUTER_FAKE_MODEL_LIST_OK')!='1':
        sys.exit(1)  # simulate broken model/list
    Path({str(self.root/'app_server_launches.txt')!r}).open('a').write('start\\n')
    for line in sys.stdin:
        request=json.loads(line)
        if 'id' not in request: continue
        if request.get('method')=='initialize':
            answer={{'id':request['id'],'result':{{}}}}
        elif request.get('method')=='model/list':
            answer={{'id':request['id'],'result':{{'data':[{{
                'id':'gpt-6.1-sol','model':'gpt-6.1-sol', 'hidden':False,
                'defaultReasoningEffort':'medium',
                'supportedReasoningEfforts':[{{'reasoningEffort':e}} for e in ['low','medium','high','xhigh','max']]
            }}],'nextCursor':None}}}}
        else:
            answer={{'id':request['id'],'result':{{}}}}
        print(json.dumps(answer),flush=True)
    sys.exit(0)
if args and args[0]=='exec':
    work=sys.stdin.read()
    Path({str(self.root/'codex_args.json')!r}).write_text(json.dumps(args))
    Path({str(self.root/'submitted_task.txt')!r}).write_text(work,encoding='utf-8')
    print(json.dumps({{'type':'thread.started','thread_id':'fake-thread'}},ensure_ascii=False))
    print(json.dumps({{'type':'item.completed','item':{{'type':'agent_message','text':'worker completed'}}}},ensure_ascii=False))
    print(json.dumps({{'type':'turn.completed'}},ensure_ascii=False))
    sys.exit(0)
sys.exit(2)
''', encoding='utf-8')
        fake.chmod(0o755)
        self.env={**os.environ,'PATH':str(self.bin)+os.pathsep+os.environ.get('PATH',''),
                  'OPENROUTER_API_KEY':'','LUNA_ROUTER_IGNORE_REGISTRY_KEY':'1'}

    def test_run_auto_executes_default_worker_when_app_server_is_broken(self):
        target=self.root/'result.json'
        cmd=[sys.executable,str(ROOT/'router.py'),'run','--prompt','修復 Rust 程式測試錯誤',
             '--project',str(self.root),'--routing','local','--output',str(target)]
        out=subprocess.run(cmd,cwd=self.root,env=self.env,text=True,capture_output=True,timeout=15)
        self.assertEqual(out.returncode,0,out.stderr or out.stdout)
        result=json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(result['plan']['decision_source'],'codex_cli_default_fallback')
        self.assertEqual(result['plan']['backend'],'codex_default')
        self.assertIn({'codex_discovery_error':'app_server_closed'},result['plan']['notes'])
        self.assertIsNone(result['plan']['selected_model'])
        self.assertEqual(result['execution']['thread_id'],'fake-thread')
        self.assertEqual(result['execution']['status'],'turn_completed')
        self.assertIsNone(result['execution']['actual_model'])
        self.assertFalse(result['model_switch_verified'])
        self.assertIn('修復 Rust', (self.root/'submitted_task.txt').read_text(encoding='utf-8'))

    def test_run_auto_chooses_model_when_discovery_succeeds_and_starts_once(self):
        target=self.root/'success.json'
        env={**self.env,'ROUTER_FAKE_MODEL_LIST_OK':'1'}
        cmd=[sys.executable,str(ROOT/'router.py'),'run','--prompt','修復 Rust 測試錯誤',
             '--project',str(self.root),'--offline','--output',str(target)]
        out=subprocess.run(cmd,cwd=self.root,env=env,text=True,capture_output=True,timeout=15)
        self.assertEqual(out.returncode,0,out.stderr or out.stdout)
        result=json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(result['routing_mode'],'local')
        self.assertEqual(result['plan']['selected_model'],'gpt-6.1-sol')
        self.assertEqual(result['plan']['requested_effort'],'medium')
        self.assertEqual(result['execution']['status'],'turn_completed')
        self.assertEqual(result['execution']['requested_model'],'gpt-6.1-sol')
        self.assertEqual(result['execution']['capability_source'],'recent_app_server_snapshot')
        self.assertEqual((self.root/'app_server_launches.txt').read_text().count('start'),1)
        arguments=json.loads((self.root/'codex_args.json').read_text())
        self.assertEqual(arguments[arguments.index('--model')+1], 'gpt-6.1-sol')
        self.assertIn('model_reasoning_effort="medium"', arguments)

    def test_pinned_model_is_not_replaced_with_default(self):
        task=self.root/'pinned.json'
        task.write_text(json.dumps({'objective':'Fix bug', 'kind':'coding','requested_model':'gpt-6-astra'}))
        cmd=[sys.executable,str(ROOT/'router.py'),'run','--task',str(task),'--project',str(self.root),
             '--routing','local']
        out=subprocess.run(cmd,cwd=self.root,env=self.env,text=True,capture_output=True,timeout=15)
        self.assertEqual(out.returncode,2,out.stderr)
        result=json.loads(out.stdout)
        self.assertEqual(result['plan']['status'],'blocked')
        self.assertFalse((self.root/'submitted_task.txt').exists())


if __name__=='__main__':
    unittest.main()
