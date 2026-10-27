# Luna Router v2.7.0 Visible Agent Workflow Specification

## Goal and boundaries

Provide a host-orchestrated multi-agent workflow in which a configured Luna coordinator decomposes a user goal, real Luna Decisions selects each worker model and effort, the native host starts visible agents concurrently, and a configurable integrator assembles and validates the result. The default integrator is `gpt-6.1-sol` at `high` effort.

This feature coordinates work; it does not change the model serving the current conversation. Luna Router MCP cannot create native UI agents or switch the current conversation model. Native visibility depends on the host's `spawn_agent` capability and its normal UI. A workflow may therefore return `blocked: native_host_dispatch_unavailable` when that capability is absent.

The record of execution consists of task descriptions, decisions, status transitions, native agent IDs, progress actions, tool evidence, results, and verification evidence. It must not claim to expose hidden chain-of-thought or private internal reasoning.

## User-visible entrypoints

| Entry point | Contract | Starts worker? | Result |
| --- | --- | --- | --- |
| `luna_workflow_start(project, tasks, config?)` MCP / `luna.py workflow start --request <UTF-8 JSON>` | Create a durable workflow from structured tasks and config | No | Job ID and snapshot |
| `luna_decide(job_id, task_id)` MCP / `luna.py workflow decide --request <UTF-8 JSON>` | Request one real Luna Decisions worker route | No | Route decision or blocked result |
| `luna_workflow_dispatch(job_id, task_id)` MCP / `luna.py workflow dispatch --request <UTF-8 JSON>` | Return dispatch descriptor after valid route | No; host performs native spawn | Descriptor including selected model and effort |
| `luna_workflow_event(job_id, task_id?, action, agent_id?, summary?, outcome?, evidence?)` MCP / `workflow event` | Append host-reported start/progress/completion/integration evidence | No | Durable event |
| `luna_workflow_status(job_id, after?)` MCP / `workflow status` | Read job snapshot and events after cursor | No | Job, tasks, integration/verification and events |
| `luna_workflow_integrate(job_id)` MCP / `workflow integrate` | Return/prepare integration work for configured integrator | No; host performs configured role | Integration descriptor/result |
| `luna_workflow_verify(job_id, check_file)` MCP / `workflow verify` | Run a local verifier/check definition through host | Runs check | Actual check outcome and evidence |

The quick decision entry point is intentionally decision-only: it never starts a worker, makes a workflow, or implies execution. Existing `luna_run` remains the legacy synchronous single-worker interface.

All online decisions require a successful real Luna Decisions response. The workflow API has no offline option; legacy `luna_run(live=false)` remains an explicit offline opt-out and is never represented as a Luna decision. Do not call `luna_plan`, `doctor`, `demo`, or local policy as a substitute for executing requested work.

## Configuration contract

Workflow-start config is a flat object. Defaults:

```json
{
  "coordinator_model": "gpt-6-luna",
  "coordinator_effort": "low",
  "integrator_model": "gpt-6.1-sol",
  "integrator_effort": "high",
  "max_parallel": 2
}
```

Coordinator and integrator are configured fixed roles. A user may change both through workflow-start config; defaults apply when omitted. Luna Decisions chooses each worker's model and effort. The coordinator must not substitute its own favorite model for a failed or invalid worker decision. `max_parallel` is capped by host capacity and configured limits.

## Workflow decomposition and task contract

Coordinator supplies a structured JSON task array validated before any dispatch. Each task has:

```json
{
  "id": "task-01",
  "objective": "Concrete bounded outcome",
  "kind": "coding",
  "complexity": 2,
  "risk": "low",
  "data_policy": "metadata_only",
  "write_scopes": ["src/parser.py", "tests/test_parser.py"],
  "depends_on": []
}
```

Requirements:

- Task IDs are unique within the workflow; dependency IDs must exist and form a directed acyclic graph. The persisted task also carries `status`, `plan`, `agent_id`, `outcome`, and `evidence` fields.
- Write scopes must be explicit, normalized relative paths or path prefixes bound to the workflow's absolute project root. Reject traversal, absolute paths, and ambiguous overlaps between concurrently runnable tasks. Dependencies may permit deliberate sequential ownership transfer.
- Read scope is informational and does not authorize access outside the bound project. `metadata_only` means Decisions receives fixed classification metadata, not objective text, project paths, source files, secrets, or unapproved summaries.
- Decomposition has bounded task count. Empty decomposition, malformed output, cycles, overlaps, or invalid project binding are returned as blocked validation results, not silently repaired into guessed work.
- A task becomes dispatchable only when all dependencies completed successfully and its routing decision is valid.

## Decisions and dispatch contract

For every worker task, request a real Luna Decisions route for that task's fixed classification metadata and applicable capability constraints. Persist the complete redacted decision evidence: status, model, effort, request/result IDs if provided, timestamps, and failure class. Never persist or log credential values.

Only HTTP 500/502/503/504 are retried, at most three attempts and at most five seconds between attempts; ambiguous transport failures are not retried. Authentication, quota/billing, malformed response, invalid/unsupported route, and policy rejection are terminal for that task until an explicit resume/action changes the condition. Never retry merely because a valid decision is inconvenient. Never create a successful-looking fallback decision.

After a valid route, the host dispatches using its native `spawn_agent` contract with `fork_turns="none"`, chosen `model`, chosen `reasoning_effort`, and task-scoped initial instructions containing the task objective, acceptance criteria, project binding, write ownership, dependency outputs, and the non-recursive worker rule. Record the returned native agent ID before marking the task `running`. If the host call outcome is uncertain, leave the durable task `dispatching`, report the uncertainty through the Host, and do not replay that spawn automatically because it can create duplicate writers. Native IDs are opaque host-issued strings and are not synthesized by Luna Router.

Tasks with disjoint write scopes and satisfied dependencies may dispatch in parallel up to `max_parallel`. Task execution is not accepted until the host reports a terminal native-agent result and the workflow store has recorded it. A native agent's completion is not independent verification.

## Durable workflow store and API recommendations

Use a local transactional store bound to a canonical absolute project root. Persist across process restarts. The store is the source of truth; host process memory is not. A job snapshot contains `job: {id, project, config, status, integration, verification}`, a flat `tasks` list, and ordered `events` with a sequence cursor. Each task contains `id`, `objective`, `kind`, `complexity`, `risk`, `data_policy`, `write_scopes`, `depends_on`, `status`, `plan`, `agent_id`, `outcome`, and `evidence`. Suggested tables/entities:

- `workflow`: `workflow_id`, project binding/fingerprint, objective metadata reference, state, created/updated timestamps, coordinator/integrator/verifier config snapshot, event sequence.
- `task`: `workflow_id`, `task_id`, objective/acceptance, dependencies, read/write scopes, state, decision evidence, native agent ID, result/evidence references, timestamps.
- `event`: monotonically increasing per-workflow `seq`, event ID, timestamp, actor, event type, task ID, safe structured payload.
- `action`: idempotency key, requested action, actor, status/result, timestamps.

Public MCP/CLI contract (CLI actions accept a UTF-8 JSON request file):

```python
luna_workflow_start(project, tasks, config?)
luna_decide(job_id, task_id)
luna_workflow_dispatch(job_id, task_id)
luna_workflow_event(job_id, task_id?, action, agent_id?, summary?, outcome?, evidence?)
luna_workflow_status(job_id, after?)
luna_workflow_integrate(job_id)
luna_workflow_verify(job_id, check_file)
```

Equivalent CLI actions are `workflow start|decide|dispatch|event|status|integrate|verify --request <UTF-8 JSON file>`. Mutations use transactions and idempotency where callers can race. Event sequence is monotonic; event pagination must not skip entries. Keep event payloads bounded and redact secrets. Store artifact paths relative to the project root where possible; verify resolved paths remain inside that root when reading.

MCP schemas should use closed objects (`additionalProperties: false`), require job IDs for task actions/reads, validate absolute project on start, bound page limits, and return structured `status`/`reason` instead of pretending unavailable capabilities succeeded. The host uses native tools after a dispatch descriptor is ready; MCP never claims to spawn a UI agent itself. Synchronous legacy `luna_run` semantics remain distinct.

## Status model and progress evidence

Workflow states: `executing`, `integrating`, `awaiting_verification`, `verifying`, `verification_failed`, `completed`, `integration_failed`, `integration_blocked`, `integration_unknown`. Task-level routing blockage leaves independent branches available.

Task states: `queued`, `deciding`, `ready`, `routing_blocked`, `dispatching`, `running`, `completed`, `failed`, `blocked`, `unknown`. Uncertain unregistered dispatch stays claimed and cannot be replayed.

Event actions are `started`, `progress`, `completed`, `integration_started`, and `integration_completed`. Append an event for each material state change: workflow created; routing/decision accepted/blocked/retry; dispatch descriptor returned; native ID registered; progress/tool evidence; worker result; integration started/completed; verification started/completed/failed. Include source/actor and timestamps. Progress is observable actions and tool evidence, not hidden reasoning text. Register a native ID with a `started` event before reporting task progress/completion.

Progress action examples: `read_file`, `edit_file`, `run_command`, `inspect_result`, `report_blocker`. Tool evidence includes command/tool identifier, bounded arguments summary, exit/status, output or artifact reference, and timestamp. Do not expose credentials or claim a command ran without host evidence.

## Integration, verification, and completion

When required task results are ready, `luna_workflow_integrate(job_id)` returns/prepares integration work for the configured integrator, which receives task results, changed-file summaries, acceptance criteria, and evidence references. It may resolve integration conflicts only within assigned project scope and must record its actions/results. The integrator is a configured role, not another Luna Decisions-selected worker.

An independent verifier is separate from the worker and integrator. `luna_workflow_verify(job_id, check_file)` invokes an actual local check definition through the host and records its output; raw caller-supplied PASS flags are invalid. `completed` requires successful required tasks, successful integration, and independent verifier evidence. Missing or infrastructure-failed verification remains `awaiting_verification`; a failed check is `verification_failed`; worker completion alone never means workflow completion. Never claim tests, live Decisions, native spawning, deployment, or publication occurred without direct evidence.

## Failure handling and continuation

Decision-service failure blocks only the affected task's routing and dependent tasks. It does not stop decomposition, inspection, documentation, independent non-dependent work, or other already-routable tasks. Record the exact failure class and continue work that is independent of the missing route. Workers may not silently select a fallback model or invoke the router recursively.

Bounded retries apply only to HTTP 500/502/503/504. Auth, quota, invalid decisions, and policy failures require an explicit condition change before retry. An uncertain worker/spawn outcome is never replayed automatically. Resume must inspect stored native ID/status and require an explicit action for any ambiguous spawn. Failed task dependents remain undispatchable because their dependency is not completed; independent branches continue.

## Acceptance matrix

| Capability | Required behavior | Evidence for acceptance |
| --- | --- | --- |
| Quick decision | Real Decisions request; returns route only | Decision response ID/model/effort or exact blocked reason; no worker ID |
| Durable jobs | Survive CLI/MCP process restart | Reopen store and retrieve same workflow/tasks/events |
| Decomposition | Structured, bounded DAG with scopes | Schema/DAG/scope rejection and durable valid-job tests |
| Decisions | Every worker model/effort selected by real Decisions | Redacted route record per dispatched worker |
| Native visibility | Host `spawn_agent` called and native IDs recorded | Host returned IDs and UI visibility evidence; MCP alone is insufficient |
| Parallelism | Independent disjoint writers run up to configured cap | Event timestamps/native IDs and conflict rejection evidence |
| Failures | Failed Decisions do not stop independent work; no fallback | Injected failure class plus continuing independent task evidence |
| Progress | Actions/tool evidence are queryable in order | Event cursor and tool evidence records |
| Integration config | Integrator defaults Sol/high and is configurable | Config override reflected in integration dispatch record |
| Verification | Separate verifier evidence gates completion | Verifier identity/check output; worker completion alone stays incomplete |
| Privacy | Metadata-only request excludes task text/path/secrets | Captured request payload inspection with credentials redacted |
| Honest status | Live, fixture, native-host and independent acceptance are distinct | Reports separate decision, dispatch, completion and verification fields |
