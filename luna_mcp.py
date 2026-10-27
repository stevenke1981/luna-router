#!/usr/bin/env python3
"""Small synchronous stdio MCP adapter. stdout is JSON-RPC only.

No omitted parameter becomes offline. luna_run executes; luna_plan only plans.
Requests remain synchronous; there is no background task or hidden restart.
"""
from __future__ import annotations
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import luna
import router

TASK_PROPERTIES={
    'task':{'type':'object','description':'Task object, including objective. Only fixed classification metadata is sent to Luna by default.'},
    'project':{'type':'string','description':'Absolute local project directory.'},
    'live':{'type':'boolean','default':True,'description':'Omitted/true requires online Luna. false is an explicit local opt-out, never Luna evidence.'},
    'sandbox':{'type':'string','enum':['read-only','workspace-write'],'default':'workspace-write'},
}
TOOLS=[
    {'name':'luna_run','description':'Default live=true. Call FIRST for concrete work, without asking the user: real Luna Decisions picks model and effort, then the selected Codex worker launches immediately (no approval pauses). Synchronous: returns only after the worker turn ends (can take many minutes). Never call it from inside a dispatched worker (task starts with [LUNA ROUTER WORKER]). Never silently falls back to local.',
     'inputSchema':{'type':'object','properties':TASK_PROPERTIES,'required':['task','project'],'additionalProperties':False}},
    {'name':'luna_plan','description':'Default live=true. Call real Luna Decisions and return a plan only. This does NOT run a worker; do not use as task completion.',
     'inputSchema':{'type':'object','properties':TASK_PROPERTIES,'required':['task','project'],'additionalProperties':False}},
    {'name':'luna_status','description':'Local diagnostics only; no inference and no credential values.',
     'inputSchema':{'type':'object','properties':{},'additionalProperties':False}},
]

def invoke(name, arguments):
    if not isinstance(arguments,dict):
        raise ValueError('arguments must be an object')
    if os.environ.get('LUNA_ROUTER_WORKER')=='1':
        raise ValueError('recursive_router_invocation_blocked')
    if name=='luna_status':
        if arguments:
            raise ValueError('luna_status does not accept inference flags; use luna probe explicitly')
        out=io.StringIO()
        with contextlib.redirect_stdout(out):
            code=router.main(['doctor'])
        return code,json.loads(out.getvalue())
    if name not in {'luna_run','luna_plan'}:
        raise ValueError('unknown tool')
    if set(arguments)-set(TASK_PROPERTIES):
        raise ValueError('unsupported argument')
    live=arguments.get('live',True)
    if type(live) is not bool:
        raise ValueError('live must be a boolean')
    if not isinstance(arguments.get('task'),dict):
        raise ValueError('task must be an object')
    project=arguments.get('project')
    if not isinstance(project,str) or not Path(project).is_absolute():
        raise ValueError('project must be an absolute path')
    sandbox=arguments.get('sandbox','workspace-write')
    if sandbox not in {'read-only','workspace-write'}:
        raise ValueError('invalid sandbox')
    with tempfile.TemporaryDirectory(prefix='luna-mcp-') as folder:
        task=Path(folder)/'task.json'
        report=Path(folder)/'result.json'
        router.save_json(task,arguments['task'])
        argv=['run' if name=='luna_run' else 'plan','--task',str(task),
              '--project',project,'--sandbox',sandbox,'--output',str(report)]
        if not live:
            argv.append('--offline')
        out=io.StringIO()
        with contextlib.redirect_stdout(out):
            code=router.main(argv)
        result=router.read_json(report) if report.exists() else {'status':'blocked','reason':'missing_report'}
    return code,result


def dispatch(message):
    if not isinstance(message,dict) or message.get('jsonrpc')!='2.0':
        return {'jsonrpc':'2.0','id':None,'error':{'code':-32600,'message':'Invalid JSON-RPC request'}}
    if 'id' not in message:  # initialize notifications, cancellation notifications
        return None
    ident=message['id']
    method=message.get('method')
    params=message.get('params',{})
    try:
        if not isinstance(params,dict):
            raise ValueError('params must be an object')
        if method=='initialize':
            version=params.get('protocolVersion')
            supported={'2024-11-05','2025-03-26','2025-06-18','2025-11-25'}
            result={'protocolVersion':version if version in supported else '2025-11-25',
                    'capabilities':{'tools':{}},
                    'serverInfo':{'name':'luna-router','version':router.VERSION},
                    'instructions':'Use luna_run for actual work immediately; do not ask the user to confirm the route or add review rounds. live defaults to true. Local is explicit only.'}
        elif method=='ping':
            result={}
        elif method=='tools/list':
            result={'tools':TOOLS}
        elif method=='tools/call':
            try:
                code,value=invoke(params.get('name'),params.get('arguments',{}))
            except (ValueError,OSError) as exc:
                code,value=2,{'status':'blocked','reason':str(exc)}
            result={'content':[{'type':'text','text':json.dumps(value,ensure_ascii=False)}],
                    'isError':code!=0}
        else:
            return {'jsonrpc':'2.0','id':ident,'error':{'code':-32601,'message':'Method not found'}}
        return {'jsonrpc':'2.0','id':ident,'result':result}
    except ValueError as exc:
        return {'jsonrpc':'2.0','id':ident,'error':{'code':-32602,'message':str(exc)}}


def main():
    # Codex speaks UTF-8 JSON-RPC; Windows pipes default to the ANSI code page
    # (cp950), which made non-ASCII results unreadable and the call hang.
    sys.stdin.reconfigure(encoding='utf-8', errors='replace')
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    sys.stderr.reconfigure(encoding='utf-8', errors='backslashreplace')
    for line in sys.stdin:
        try:
            message=json.loads(line)
            response=dispatch(message)
        except (ValueError,UnicodeError):
            response={'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':'Parse error'}}
        if response is not None:
            print(json.dumps(response),flush=True)  # ASCII-only wire format

if __name__=='__main__':
    main()
