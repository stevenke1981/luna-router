---
name: luna-workflow
description: Coordinate multi-step project work through durable Luna Router jobs, live Luna Decisions worker routes, and visible native Codex agents; use for implementation tasks requiring decomposition, parallel work, integration, or verification.
---

# Luna Router project workflow

Use this skill for multi-step work in this repository. Locate the repository root by walking upward from this skill's directory until `luna.py` and `config.json` are present; do not assume the shell's current directory. Keep all project paths absolute.

## Start and route work

For multi-step goals, have the configured coordinator decompose the request into bounded tasks with explicit acceptance criteria, `depends_on`, and disjoint `write_scopes`. Keep independent branches running if one Decisions request is blocked. `metadata_only` is the default: Decisions receives fixed classification metadata, not task prose, source, paths, secrets, or unapproved summaries.

Put task-specific acceptance criteria in `objective`; there is no separate `acceptance` field.

Read the `workflow` section of `<root>/config.json` for the coordinator and integrator settings. A fresh trusted repository session defaults to Luna through `.codex/config.toml`. If the current host is not already that coordinator, start a visible native coordinator using those configured `model`/`reasoning_effort` values and `fork_turns="none"`, with the user's goal and this skill path. Label that role `user_configured_coordinator`, not a Decisions-selected worker. Do not recursively start another coordinator from a coordinator.

Use MCP `luna_workflow_start(project, tasks, config?)` or CLI `python <root>/luna.py workflow start --request <UTF-8 JSON file>`. Start input has:

```json
{
  "project": "E:\\absolute\\project",
  "tasks": [
    {
      "id": "task-01",
      "objective": "A bounded outcome",
      "kind": "coding",
      "complexity": 2,
      "risk": "low",
      "data_policy": "metadata_only",
      "write_scopes": ["src/parser.py", "tests/test_parser.py"],
      "depends_on": []
    }
  ],
  "config": {
    "coordinator_model": "gpt-6-luna",
    "coordinator_effort": "low",
    "integrator_model": "gpt-6.1-sol",
    "integrator_effort": "high",
    "max_parallel": 2
  }
}
```

Omit config fields to use the configured defaults. Coordinator and integrator are fixed configured roles; Luna Decisions chooses every worker model and effort. Do not claim Decisions selected the coordinator or integrator. The default integrator is `gpt-6.1-sol` / `high`; the default coordinator is `gpt-6-luna` / `low`.

For one routing question without execution, use MCP `luna_decide(job_id, task_id)` or CLI `workflow decide --request <UTF-8 JSON file>`. A valid result only records the route. It does not dispatch a worker. A worker may start only after an actual successful Decisions response.

## Dispatch visible native workers

After a task has a valid decision, call `luna_workflow_dispatch(job_id, task_id)` or CLI `workflow dispatch --request <UTF-8 JSON file>`. Use the returned dispatch descriptor exactly: call the host's native `spawn_agent` with `fork_turns="none"`, `model=<descriptor.model>`, and `reasoning_effort=<descriptor.effort>`. Pass only that task's objective, acceptance criteria, project binding, assigned write scopes, dependency results, and the instruction not to recursively invoke Luna Router.

Record the host-returned native agent ID with `luna_workflow_event(job_id, task_id, action="started", agent_id=...)` before reporting progress or completion. Only dispatch independent tasks whose write scopes do not overlap, and honor `max_parallel`. If the native host tool is unavailable or its spawn result is uncertain, report that fact; never substitute a hidden CLI worker or replay an uncertain spawn.

An uncertain spawn remains `dispatching` with its durable claim. No `dispatch_unknown` action exists. Report uncertainty in the Host conversation and inspect native Host status; do not send `started` without an actual ID, and do not send `progress` before `started`.

Native host tool activity is the source for visible agent actions. The workflow ledger reports host-provided summaries and evidence; it does not expose hidden reasoning or turn a background process into a native UI agent.

## Report progress and results

Use `luna_workflow_event` with `action="progress"` for concise observable actions and tool evidence, and `action="completed"` with the worker's outcome/evidence after the native agent turn has actually ended. Do not report a tool call before it runs, do not put model reasoning or speculative claims in progress summaries, and do not mark completion as acceptance.

Event input fields: `job_id`, optional `task_id`, `action`, optional `agent_id`, `summary`, `outcome`, and `evidence`. Supported actions are `started`, `progress`, `completed`, `integration_started`, and `integration_completed`. Keep evidence bounded and redact credentials. Use `luna_workflow_status(job_id, after?)` to retrieve the durable job snapshot and events after a sequence cursor.

Each action accepts ONLY its listed fields; do not repeat `agent_id` on completion. `outcome` is an enum: `completed`, `failed`, `blocked`, or `unknown`. Put prose in `evidence`, never in `outcome`.

| Action | Required fields besides `job_id` and `action` | Optional fields |
| --- | --- | --- |
| `started` | `task_id`, `agent_id` | none |
| `progress` | `task_id`, `summary` | none |
| `completed` | `task_id`, `outcome` | `evidence` |
| `integration_started` | `agent_id` | none |
| `integration_completed` | `outcome` | `evidence` |

## Continue through routing failures

Record the actual Decisions failure. The quick decision entrypoint retries HTTP 500/502/503/504 at most three attempts, with at most five seconds between attempts. Authentication, quota, ambiguous transport failures and malformed/invalid decisions are not automatically retried. Do not silently select a worker, rewrite a blocked decision as successful, or recursively call the router from a worker.

An API outage does not stop independent planning, repository inspection, coordinator-owned work, or integrator-owned work that can proceed from existing artifacts. A worker without a valid Decisions route remains undispatched. Continue independent tasks and record blocked tasks/dependents honestly. Never replay a spawn with an uncertain outcome.

## Integrate and verify

When all worker turns have completed, call `luna_workflow_integrate(job_id)` (CLI `workflow integrate`) to claim final integration. Spawn the returned configured integrator descriptor, default `gpt-6.1-sol` / `high`, and record `integration_started` and `integration_completed` events with its native ID and evidence. Integration is not independent verification.

Verify through MCP `luna_workflow_verify(job_id, check_file)` or CLI `workflow verify --request <UTF-8 JSON file>`. `check_file` selects the local verifier/check definition; the Host runs it and records actual output. Never submit a raw PASS flag or claim a check ran without host evidence. The workflow is complete only when required task results, integration, and independent verification all have successful evidence. Otherwise report partial/blocked status and remaining acceptance criteria.

The check file is UTF-8 JSON, for example `{"argv":["python","-m","unittest"],"timeout_seconds":300}`; select a command that actually tests the task. It runs without a shell in the project directory. Stored evidence is the exit code and bounded output hash, not raw output. A verification claim prevents simultaneous or uncertain replay. Infrastructure failures are saved without acceptance; retry only after diagnosing the failure. A completed job cannot run its verifier again.

## MCP and CLI request convention

MCP tools are `luna_workflow_start(project, tasks, config?)`, `luna_decide(job_id, task_id)`, `luna_workflow_dispatch(job_id, task_id)`, `luna_workflow_event(job_id, task_id?, action, agent_id?, summary?, outcome?, evidence?)`, `luna_workflow_status(job_id, after?)`, `luna_workflow_integrate(job_id)`, and `luna_workflow_verify(job_id, check_file)`. The equivalent CLI actions are `workflow start|decide|dispatch|event|status|integrate|verify --request <UTF-8 JSON file>`.

CLI request files contain exactly the same JSON object as MCP arguments. Use absolute request-file and `check_file` paths, since relative paths resolve against the service process working directory, not necessarily the project. Minimal examples after start:

```json
{"job_id":"<job-id>","task_id":"task-01"}
```

Use that object for `decide` and `dispatch`. For the following operations:

```text
event:     {"job_id":"<job-id>","task_id":"task-01","action":"started","agent_id":"<native-id>"}
event:     {"job_id":"<job-id>","task_id":"task-01","action":"completed","outcome":"completed","evidence":"Observed result"}
integrate: {"job_id":"<job-id>"}
event:     {"job_id":"<job-id>","action":"integration_started","agent_id":"<integrator-id>"}
event:     {"job_id":"<job-id>","action":"integration_completed","outcome":"completed","evidence":"Observed integration"}
verify:    {"job_id":"<job-id>","check_file":"E:\\project\\checks.json"}
status:    {"job_id":"<job-id>","after":0}
```

The host's native agent interface controls agent creation and UI visibility. Luna Router does not switch the current conversation's model. Report separately: Decisions response, native dispatch/ID, worker turn completion, integration, and independent verification.
