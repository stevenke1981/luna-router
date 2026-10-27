"""Quick host-facing operations for visible native agents, not hidden workers."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid

import codex_backend
from credentials import resolve_key
from ledger import Ledger
from policy import normalize_task
import router
from verifier import read_check, run_check
from workflow import WorkflowStore


FIELDS = {
    'start': ({'project', 'tasks'}, {'config'}),
    'decide': ({'job_id', 'task_id'}, set()),
    'dispatch': ({'job_id', 'task_id'}, set()),
    'event': ({'job_id', 'action'}, {'task_id', 'agent_id', 'summary', 'outcome', 'evidence'}),
    'status': ({'job_id'}, {'after'}),
    'integrate': ({'job_id'}, set()),
    'verify': ({'job_id', 'check_file'}, set()),
}


def _request(action, args):
    if action not in FIELDS or not isinstance(args, dict):
        raise ValueError('invalid_workflow_request')
    required, optional = FIELDS[action]
    if not required <= set(args) or set(args) - required - optional:
        raise ValueError('invalid_workflow_fields')
    if action != 'status' and os.environ.get('LUNA_ROUTER_WORKER') == '1':
        raise ValueError('recursive_router_invocation_blocked')


def _native_capability(model, effort):
    runtime = codex_backend.discover()
    if runtime.get('simulation') is not False or runtime.get('auth_mode') != 'chatgpt':
        raise ValueError('verified_chatgpt_capabilities_required')
    available = next((m for m in runtime.get('models', []) if m.get('id') == model), None)
    if available is None or effort not in available.get('efforts', []):
        raise ValueError('configured_native_model_or_effort_unavailable')
    return runtime


def decide(store, job_id, task_id, config, state_dir):
    snapshot = store.begin_decision(job_id, task_id)
    task = next(t for t in snapshot['tasks'] if t['id'] == task_id)
    plan = {'status': 'blocked', 'stage': 'decision', 'reason': 'decision_not_completed',
            'decision_source': 'not_called', 'decision_model_called': False,
            'decision_response_received': False, 'decision_call_attempted': False}
    try:
        if not resolve_key()[0]:
            raise ValueError('openrouter_key_missing_online_required')
        if config.get('decision', {}).get('model') != router.DECISION_MODEL or config['decision'].get('enabled') is not True:
            raise ValueError('luna_decision_configuration_invalid')
        runtime = codex_backend.discover()
        if runtime.get('source') != 'codex.model/list' or runtime.get('simulation') is not False or runtime.get('auth_mode') != 'chatgpt':
            raise ValueError('verified_chatgpt_capabilities_required')
        # A native agent needs local tools; never select an OpenRouter text worker.
        normalized = normalize_task({k: task[k] for k in ('objective', 'kind', 'complexity', 'risk')})
        normalized.update(needs_tools=True, data_policy='metadata_only', summary_approved=False)
        ledger = Ledger(Path(state_dir) / 'decision-usage.sqlite', config['policy'])
        plan = router.make_plan(normalized, config, runtime, {'data': []}, {'data': []},
                                online=True, ledger=ledger, job=uuid.uuid4().hex,
                                auto_metadata=True, require_luna=True, fresh_decision=True,
                                decision_retry_attempts=3)
        plan.pop('worker', None)
    except (ValueError, OSError, sqlite3.Error, codex_backend.CodexBackendError) as exc:
        # Our own fixed codes only; never return raw OS details or external text.
        reason = str(exc) if isinstance(exc, (ValueError, codex_backend.CodexBackendError)) else 'decision_environment_error'
        if not reason.replace('_', '').isalnum() or len(reason) > 100:
            reason = 'invalid_decision_request'
        plan.update(reason=reason)
    attempts = []
    http_status = None
    for note in plan.get('notes', []):
        if not isinstance(note, dict):
            continue
        if type(note.get('http_status')) is int:
            http_status = note['http_status']
        for item in note.get('decision_attempts', []):
            if not isinstance(item, dict):
                continue
            safe = {key: item[key] for key in ('attempt', 'http_status') if type(item.get(key)) is int}
            if item.get('status') in {'requested', 'failed', 'response_received'}:
                safe['status'] = item['status']
            if type(item.get('will_retry')) is bool:
                safe['will_retry'] = item['will_retry']
            attempts.append(safe)
    try:
        saved = store.record_decision(job_id, task_id, plan)
    except ValueError:
        # Reject invalid decision data without stranding the task as deciding.
        plan = {'status': 'blocked', 'stage': 'decision', 'reason': 'invalid_luna_decision',
                'decision_source': 'luna_decisions_rejected'}
        saved = store.record_decision(job_id, task_id, plan)
    plan = next(t['plan'] for t in saved['tasks'] if t['id'] == task_id)
    return {'job_id': job_id, 'task_id': task_id, 'plan': plan,
            'decision_attempts': attempts, 'http_status': http_status,
            'dispatch_ready': plan.get('status') == 'planned', 'worker_started': False,
            'continuation': 'dispatch_native_agent' if plan.get('status') == 'planned' else 'continue_independent_work'}


def _event(store, args):
    action, job = args['action'], args['job_id']
    task = args.get('task_id')
    expected = {
        'started': {'task_id', 'agent_id'}, 'progress': {'task_id', 'summary'},
        'completed': {'task_id', 'outcome'}, 'integration_started': {'agent_id'},
        'integration_completed': {'outcome'},
    }
    if action not in expected or not expected[action] <= set(args):
        raise ValueError('invalid_workflow_event')
    allowed = {'job_id', 'action'} | expected[action]
    if action in {'completed', 'integration_completed'}:
        allowed.add('evidence')
    if set(args) - allowed:
        raise ValueError('unexpected_workflow_event_fields')
    if action == 'started':
        return store.started(job, task, args['agent_id'])
    if action == 'progress':
        return store.progress(job, task, args['summary'])
    if action == 'completed':
        return store.complete(job, task, args['outcome'], args.get('evidence', ''))
    if action == 'integration_started':
        return store.integration_started(job, args['agent_id'])
    return store.integration_complete(job, args['outcome'], args.get('evidence', ''))


def handle(action, args, *, state_dir=None, config=None):
    _request(action, args)
    state_dir = Path(state_dir) if state_dir is not None else router.ROOT / '.state' / 'workflow'
    config = config if config is not None else router.read_json(router.ROOT / 'config.json')
    store = WorkflowStore(state_dir)
    if action == 'start':
        if not isinstance(args.get('config', {}), dict):
            raise ValueError('workflow_config_must_be_an_object')
        settings = {**config.get('workflow', {}), **args.get('config', {})}
        return store.create(args['project'], args['tasks'], settings)
    if action == 'decide':
        return decide(store, args['job_id'], args['task_id'], config, state_dir)
    if action == 'dispatch':
        descriptor = store.claim_dispatch(args['job_id'], args['task_id'])
        descriptor['host_action'] = 'spawn_agent'
        descriptor['fork_turns'] = 'none'
        descriptor['reasoning_effort'] = descriptor['effort']
        descriptor['worker_started'] = False
        descriptor['instructions'] = ('Dispatch this descriptor through native agent tools, then record its ID. '
                                      'Do not replay this claim if the spawn outcome is uncertain.')
        return descriptor
    if action == 'event':
        return _event(store, args)
    if action == 'status':
        return store.snapshot(args['job_id'], after=args.get('after', 0))
    if action == 'integrate':
        snapshot = store.snapshot(args['job_id'])
        settings = snapshot['job']['config']
        _native_capability(settings['integrator_model'], settings['integrator_effort'])
        descriptor = store.claim_integration(args['job_id'])
        descriptor.update(host_action='spawn_agent', fork_turns='none',
                          reasoning_effort=descriptor['effort'], worker_started=False)
        return descriptor
    if action == 'verify':
        # Claim before any external command; a lost response must never replay it.
        snapshot = store.claim_verification(args['job_id'])
        try:
            contract = read_check(args['check_file'])
        except (ValueError, OSError):
            result = {'outcome': 'blocked', 'evidence_kind': 'worker',
                      'reason': 'verifier_contract_invalid'}
        else:
            try:
                result = run_check(contract, snapshot['job']['project'])
            except (ValueError, OSError):
                result = {'outcome': 'blocked', 'evidence_kind': 'worker',
                          'reason': 'verifier_environment_error'}
        if result.get('evidence_kind') != 'verifier':
            saved = store.verification_unavailable(args['job_id'], result)
            saved.update(status='blocked', continuation='repair_verifier_without_replaying_workers')
            return saved
        return store.verify(args['job_id'], result)
    raise ValueError('unknown_workflow_action')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=sorted(FIELDS))
    parser.add_argument('--request', required=True, help='UTF-8 JSON request file; same fields as MCP')
    parser.add_argument('--state-dir', type=Path, help='Explicit host-local workflow store')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        result = handle(args.action, router.read_json(args.request), state_dir=args.state_dir)
        code = 2 if (result.get('status') == 'blocked' or result.get('plan', {}).get('status') == 'blocked' or
                     result.get('job', {}).get('status') in {'verification_failed', 'integration_failed',
                                                          'integration_blocked', 'integration_unknown'}) else 0
    except (ValueError, OSError, sqlite3.Error, codex_backend.CodexBackendError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        result = {'status': 'blocked', 'reason': reason, 'continuation': 'continue_independent_work'}
        code = 2
    if args.output:
        router.save_json(args.output, result)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
