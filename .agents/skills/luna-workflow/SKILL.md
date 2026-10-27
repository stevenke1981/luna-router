---
name: luna-workflow
description: Let native gpt-6-luna medium directly choose Sol or Terra workers and their effort, dispatch visible agents, and independently verify project work.
---

Locate the repository root by walking upward from this skill until `luna.py` and `config.json` are present. Keep project and request paths absolute.

# Native Luna coordinator workflow

Use `gpt-6-luna` at `medium` as the native coordinator and decision maker. It directly chooses each worker from `gpt-6.1-sol` and `gpt-6.1-terra`, and one supported effort in `low`, `medium`, `high`, `xhigh`, `max`. Do not call OpenRouter Decisions for this workflow, invent a choice, or replace a blocked selection with heuristics. Do not ask the user to select models for you.

## Start and select

Read `config.json` in the runtime root. `workflow.coordinator_model` / `coordinator_effort` default to `gpt-6-luna/medium`; the native decision contract requires this pair. If the Host is not already this coordinator, create a visible native agent using `fork_turns="none"`, `model="gpt-6-luna"`, `reasoning_effort="medium"`. Label this as a user-configured coordinator. Give it the user's objective, task context, available choices and this skill. Never recursively create another coordinator. Use the actual Host agent ID; do not fabricate an ID when the Host cannot supply one.

The coordinator decomposes the goal into tasks with explicit acceptance criteria, dependencies and disjoint write scopes, and returns its actual model/effort selection for each. A new trusted project session uses `.codex/config.toml`; this never changes an already-running Host's model.

Call `luna_workflow_start` with:

```json
{"project":"E:\\project","tasks":[{"id":"task-01","objective":"Implement and verify the requested behavior","kind":"coding","complexity":2,"risk":"low","data_policy":"metadata_only","write_scopes":["src/parser.py","tests/test_parser.py"],"depends_on":[]}]}
```

The optional `config` contains coordinator/integrator model and effort plus `max_parallel` (default 2). Integration defaults to `gpt-6.1-sol/high` and is a configured role, not a coordinator-selected worker.

After receiving the native coordinator's real response, call `luna_decide` with its selection:

```json
{"job_id":"<job-id>","task_id":"task-01","coordinator_agent_id":"<actual-native-coordinator-id>","selected_model":"gpt-6.1-sol","requested_effort":"high"}
```

MCP validates live Codex model/effort availability and records this as `native_coordinator_host_reported`. It does not run inference or verify the model behind a Host agent ID. Do not describe this receipt as an OpenRouter response or fabricate API response IDs. Missing selections are errors; there is no implicit online fallback. Unavailable worker pairs stay blocked; continue independent work and ask the coordinator to reconsider only after resolving the cause.

## Claim and execute

Call `luna_workflow_dispatch` with `job_id,task_id`. Use the returned descriptor exactly with native `spawn_agent`: `fork_turns="none"`, `model=<model>`, `reasoning_effort=<effort>`. Give the worker explicit file ownership and acceptance criteria, tell it others share the codebase, and prepend `[LUNA ROUTER WORKER]` so it does not recursively route. Do not silently substitute a CLI process for a visible agent.

Record the actual Host agent ID through `luna_workflow_event(action="started")` before progress/completion. Write scopes must remain disjoint and dependencies complete. A dispatch claim is single-use: if spawn outcome is uncertain, inspect Host state and report it rather than replaying the call. Agent IDs and role/model labels are Host-reported evidence, not provider attestation.

## Events and independent acceptance

Use `luna_workflow_status(job_id,after?)` for durable snapshots. Event schemas are strict:

| action | required besides job_id/action | optional |
| --- | --- | --- |
| started | task_id, agent_id | none |
| progress | task_id, summary | none |
| completed | task_id, outcome | evidence |
| integration_started | agent_id | none |
| integration_completed | outcome | evidence |

`outcome` is one of `completed`, `failed`, `blocked`, `unknown`. Never use prose as the enum. Record only observed tool activity/results, not hidden reasoning. An uncertain spawn without an agent ID remains dispatching; do not record started/progress.

After all worker turns complete, call `luna_workflow_integrate(job_id)`, spawn the returned configured integrator natively and record its start/completion. Integration may resolve actual defects without starting repeated review rounds.

Finally call `luna_workflow_verify(job_id,check_file)` with an absolute UTF-8 JSON file such as `{"argv":["python","-X","utf8","-m","unittest","discover","-s","tests"],"timeout_seconds":300}`. The verifier executes without a shell in the project directory and persists actual exit status and output hash. Worker completion, integration prose and raw PASS flags are not independent acceptance. Never replay an uncertain verification; diagnose it first.

## CLI and compatibility

The same JSON arguments work with `workflow start|decide|dispatch|event|status|integrate|verify --request <absolute UTF-8 JSON file>`. Use the installed absolute entrypoint if MCP exposes stale schemas or is unavailable. Restart the Host to load new MCP schemas; do not claim an existing tool list updated itself.

Legacy `luna_decide_online` / CLI `workflow decide-online`, `luna_run`, `luna_plan` and `probe` still use OpenRouter when explicitly invoked. They are separate opt-in compatibility paths, not the direct native coordinator workflow. No API key is needed for native selection registration. Codex itself still uses the user's OpenAI connection. Legacy external decisions retain metadata-only boundaries and bounded transient retries; credentials must never be printed.

Report the native coordinator's selected model/effort and Host ID, worker dispatch/completion, integration and actual verification separately. A worker beginning `[LUNA ROUTER WORKER]` or running with `LUNA_ROUTER_WORKER=1` performs its assignment directly and must not call the router.
