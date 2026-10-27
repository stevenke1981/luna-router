"""MCP contracts for quick decisions and native-host workflow coordination."""
import sqlite3

STRING = {'type': 'string'}
IDENTITY = {'job_id': STRING, 'task_id': STRING}
EVENT_PROPERTIES = {'job_id': STRING, 'task_id': STRING,
                    'action': {'type': 'string', 'enum': ['started', 'progress', 'completed',
                               'integration_started', 'integration_completed']},
                    'agent_id': STRING, 'summary': STRING, 'outcome': STRING, 'evidence': STRING}


def _tool(name, action, description, properties, required):
    return {'name': name, 'description': description,
            'inputSchema': {'type': 'object', 'properties': properties,
                            'required': required, 'additionalProperties': False}}


TOOLS = [
    _tool('luna_workflow_start', 'start',
          'Create a local native-agent workflow with explicit project, tasks and configurable Luna coordinator/Sol high integrator. Does not launch agents.',
          {'project': STRING, 'tasks': {'type': 'array', 'items': {'type': 'object'}, 'minItems': 1},
           'config': {'type': 'object', 'properties': {
               'coordinator_model': STRING, 'coordinator_effort': STRING,
               'integrator_model': STRING, 'integrator_effort': STRING,
               'max_parallel': {'type': 'integer', 'minimum': 1, 'maximum': 16}},
               'additionalProperties': False}}, ['project', 'tasks']),
    _tool('luna_decide', 'decide',
          'Quick real Luna Decisions model/effort selection for one workflow task. No worker is started. Transient HTTP errors have bounded retries; continue independent work when blocked.',
          IDENTITY, ['job_id', 'task_id']),
    _tool('luna_workflow_dispatch', 'dispatch',
          'Atomically claim a ready task and return model/effort/ownership for native spawn_agent. Caller must spawn visibly, then record agent ID. Never replay an uncertain claim.',
          IDENTITY, ['job_id', 'task_id']),
    _tool('luna_workflow_event', 'event',
          'Record observed native-agent ID, progress or actual turn completion. Host-reported evidence only; completion is not independent verification.',
          EVENT_PROPERTIES, ['job_id', 'action']),
    _tool('luna_workflow_status', 'status',
          'Read durable task states and incremental native-agent event summaries after a cursor. No inference; tool actions remain visible in the native host UI.',
          {'job_id': STRING, 'after': {'type': 'integer', 'minimum': 0}}, ['job_id']),
    _tool('luna_workflow_integrate', 'integrate',
          'After all workers complete, claim configured final integration (default gpt-6.1-sol/high). Return a native dispatch descriptor; no hidden process is started.',
          {'job_id': STRING}, ['job_id']),
    _tool('luna_workflow_verify', 'verify',
          'Run the explicitly supplied local verifier after integration, recording actual exit code and evidence hashes. Never accept a bare PASS flag.',
          {'job_id': STRING, 'check_file': STRING}, ['job_id', 'check_file']),
]
ACTIONS = dict(zip((tool['name'] for tool in TOOLS),
                   ('start', 'decide', 'dispatch', 'event', 'status', 'integrate', 'verify')))


def invoke(name, arguments):
    from workflow_service import handle
    import codex_backend
    try:
        result = handle(ACTIONS[name], arguments)
        failed = (result.get('status') == 'blocked' or result.get('plan', {}).get('status') == 'blocked'
                  or result.get('job', {}).get('status') in {'verification_failed', 'integration_failed',
                                                          'integration_blocked', 'integration_unknown'})
        return (2 if failed else 0), result
    except (ValueError, OSError, sqlite3.Error, codex_backend.CodexBackendError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        return 2, {'status': 'blocked', 'reason': reason, 'continuation': 'continue_independent_work'}
