# Native coordinator selection contract

The configured `gpt-6-luna/medium` native coordinator decomposes the task and directly selects worker model/effort. Worker models are exactly `gpt-6.1-sol` and `gpt-6.1-terra`; efforts are `low`, `medium`, `high`, `xhigh`, `max`. Live Codex model/list must advertise both the coordinator pair and the selected worker pair. The tool never silently changes a selection or manufactures a model response.

## Interfaces and provenance

- `luna_workflow_start(project,tasks,config?)` creates a durable workflow. Tasks include objective (including acceptance criteria), kind, complexity, risk, metadata_only, explicit write_scopes, and depends_on. An empty write scope is read-only.
- `luna_decide(job_id,task_id,coordinator_agent_id,selected_model,requested_effort)` validates and records an actual Host-observed coordinator response. It does not run inference, contact OpenRouter, or read an API key. Missing selections are rejected without network calls.
- `luna_workflow_dispatch(job_id,task_id)` atomically claims dispatch and returns the exact selected native model/effort. Host creates the agent and records its real ID with an event. An uncertain claim/spawn must not be replayed.
- `luna_workflow_event` records observable progress/completion; `luna_workflow_status` reads snapshots/events.
- `luna_workflow_integrate` claims the separately configured integrator, default Sol/high. Host launches and records this native role.
- `luna_workflow_verify(job_id,check_file)` executes the local argv check once and persists actual acceptance evidence; raw PASS is invalid.

Native plans store `decision_source=native_coordinator_host_reported`, `backend=codex`, `simulation=false`, selected_model, requested_effort, and coordinator `{agent_id,model,effort,provenance:host_reported}`. Coordinator values must match the saved job and gpt-6-luna/medium. API call/response/metrics fields are invalid for this provenance. Host IDs and model labels are not provider attestation. Capability discovery establishes availability, not which wire model produced an answer.

CLI `workflow <action> --request <absolute UTF-8 JSON>` uses the same fields. MCP only records/validates/coordinates; it does not create native UI agents or change the currently active conversation model. Restart the Host after upgrading MCP schemas.

## State and error handling

SQLite write transactions preserve task dependencies, write-scope conflict checks, max_parallel, single-use dispatch/integration/verification claims and bounded event history. Invalid native selection input or capability failure must not strand a task in deciding. Claimed actions with an uncertain outcome are never automatically replayed. Continue independent work while reporting the actual blocked condition.

A worker turn and integrator turn are not independent acceptance. A completed job requires all required workers, successful integration and an actual passing verifier. Preserve separate verification_failed and infrastructure-unavailable states. Workers with the router worker header execute their assignments without recursively invoking the workflow.

## Compatibility and data boundary

Existing persisted Decisions plans remain readable. Legacy API-backed selection is explicitly `luna_decide_online` / `workflow decide-online`; legacy synchronous `luna_run`, `luna_plan` and probe retain their semantics. They do not substitute for native selection, and their external-call/data/fee rules remain scoped to those paths. Native selection sends no task metadata to OpenRouter; native Codex agents still use the user's OpenAI connection.

Configuration defaults: coordinator Luna/medium, integrator Sol/high, max_parallel 2. A changed coordinator config is rejected by the direct native decision contract until the user-selected protocol is deliberately updated; it is not silently substituted. Historical workflow evidence is historical, not proof of new execution.

## Acceptance

1. Allow both workers and each supported low..max level; reject other models and ultra.
2. Validate actual model/list capabilities and coordinator identity fields; no API access for native routes.
3. Round-trip native selection provenance into dispatch after process restart and retain duplicate-claim rejection.
4. Prove worker completion alone cannot complete the job; actual independent verifier is required.
5. Exercise a real native Luna selection, Host dispatch and integration, then independent verification. Record honest Host evidence without claiming provider-attested wire models.
