---
name: gpt-luna-decision
description: Route project work through real online Luna Decisions and execute through the configured Luna workflow or selected Codex worker; use for concrete tasks needing implementation, coordination, or a live route.
metadata:
  short-description: Route and execute project work with Luna Decisions
---

# Luna Decisions and workflow execution

Use real Luna Decisions for worker routes. Default online mode is `live=true`; default data policy is `metadata_only`. Never invent a route, replace a failed decision with local policy, or claim a fixture is live evidence. Do not ask the user whether to route or which model/effort to use when this skill applies.

## Choose the operation

- For a multi-step project goal, start a durable workflow with `luna_workflow_start(project, tasks, config?)` or CLI `workflow start --request <UTF-8 JSON file>`.
- For a route-only question, use `luna_decide(job_id, task_id)` or CLI `workflow decide --request <UTF-8 JSON file>`. This returns a route and never starts a worker.
- Dispatch only after a successful real decision, using `luna_workflow_dispatch(job_id, task_id)` or CLI `workflow dispatch --request <UTF-8 JSON file>`.
- For a focused single worker task, preserve the existing `luna_run` CLI path when the project does not need a multi-agent workflow.

Use the installed CLI with its absolute executable and engine paths if MCP tools are unavailable:

```powershell
& '{{PYTHON_PATH}}' '{{ROUTER_PATH}}' workflow start --request '<UTF-8 JSON file>'
```

The installer substitutes `{{PYTHON_PATH}}` and `{{ROUTER_PATH}}`. Do not use an older copy from another folder. Existing `luna_run` remains synchronous and returns only after its worker turn ends.

## Native agent dispatch

The host, not MCP, creates visible native UI agents. For each task with a valid Decisions result, use its dispatch descriptor to call native `spawn_agent` with `fork_turns="none"`, `model=<descriptor.model>`, and `reasoning_effort=<descriptor.effort>`. Do not silently choose a fallback model. Record the host's native agent ID before marking the task running. If spawn outcome is uncertain, record it and do not replay the call.

Coordinator and integrator are configured fixed roles, not Decisions-selected workers. Defaults are coordinator `gpt-6-luna` / `low`, integrator `gpt-6.1-sol` / `high`, and `max_parallel=2`; workflow config may override these. Luna Decisions selects each worker model and effort. Keep concurrently running tasks' write scopes disjoint and respect dependencies and the parallel limit.

Read the installed runtime's `config.json` workflow section. If the host is not already the configured coordinator, start that visible native role with its configured model/effort and this skill path; label it `user_configured_coordinator`. A coordinator must not start another coordinator recursively. Project-local `.agents/skills/luna-workflow/SKILL.md`, when present, provides the complete request examples.

## Durable progress and completion

Record observable host actions and tool evidence with `luna_workflow_event(job_id, task_id?, action, agent_id?, summary?, outcome?, evidence?)`. Use actions `started`, `progress`, `completed`, `integration_started`, and `integration_completed`. Read snapshots/events with `luna_workflow_status(job_id, after?)`. These records contain actions, statuses, and evidence, not hidden chain-of-thought. Host-native UI logs are the source for visible agent activity; ledger summaries do not make a background CLI process into a native agent.

CLI request files contain the same JSON object as MCP arguments; use absolute file paths. Put acceptance criteria in each task's `objective`. Event fields are action-specific: `started` requires `task_id,agent_id`; `progress` requires `task_id,summary`; `completed` requires `task_id,outcome` and permits `evidence`; `integration_started` requires only `agent_id`; `integration_completed` requires `outcome` and permits `evidence`. All require `job_id,action`; reject other fields. `outcome` must be `completed`, `failed`, `blocked`, or `unknown`, never prose. Example: `{"job_id":"<id>","task_id":"task-01","action":"completed","outcome":"completed","evidence":"Observed result"}`. An uncertain spawn remains `dispatching`; without a native ID, report in the Host conversation and do not send `started` or `progress`.

After task results, claim `luna_workflow_integrate(job_id)` and spawn the returned configured integrator descriptor, then record integration events. Run independent checks through `luna_workflow_verify(job_id, check_file)`; verification invokes a local check definition and records actual exit code and output hash. The check definition is `{"argv":["python","-m","unittest"],"timeout_seconds":300}` with a command appropriate to the project. Commands run without a shell in the project directory. A worker's completed turn or an integrator summary alone is not acceptance. Claim workflow completion only when required tasks, integration, and independent verification have successful evidence.

## Failure behavior and data boundary

The quick decision operation retries only HTTP 500/502/503/504, at most three attempts and at most five seconds between attempts. Authentication, quota, ambiguous transport failures, malformed responses, invalid decisions, and policy failures remain blocked until the condition changes. Continue independent coordinator/integrator work and other branches when a worker route is blocked, but never dispatch a worker without a valid decision. Never recursively invoke the router from a `[LUNA ROUTER WORKER]` task.

For `metadata_only`, send fixed classification metadata only, not objective text, project path, source, secrets, or unapproved summaries. `live:false` is an explicit offline request and must not be reported as a Luna decision. Read API keys from process or Windows User/System environment only; never print the key or inspect credential files.

Report these stages separately: real Decisions response, native agent dispatch and ID, worker turn completion, integration, and independent verification. Luna Router controls launched workers and does not switch the current conversation model.
