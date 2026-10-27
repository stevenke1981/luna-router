---
name: gpt-luna-decision
description: Execute tasks through required-online GPT-6 Luna Decisions, then a real Codex worker. Luna picks model and effort instantly; dispatch without asking the user or adding review rounds. Default live=true. Never silently fall back to offline or stop at a plan.
---
# GPT Luna Decisions — Required Online v2.7.0

## Decide and dispatch immediately — no asking, no review loop
Call `luna_run` as the FIRST action for a concrete work request. Do not ask the
user to confirm the route, the model, the effort, the Decisions API call, or the
data policy; the defaults (live=true, metadata_only) are already authorized.
Luna's valid answer IS the decision — both model and reasoning effort: the
engine dispatches the selected worker at once with Luna's effort (no local
complexity floor), even when Luna's probabilities are low (noted as
`luna_low_certainty_accepted`). Do not re-plan, second-guess, or call
`luna_plan` first. The Codex worker runs with `approval_policy="never"` inside
its sandbox, so it does not stop for approval. After the worker finishes, run
the task's own tests once; do not start extra review rounds. Only stop and
report when the engine returns `blocked` (missing key, API/HTTP error, invalid
answer) — report the reason instead of asking what to do.

## Synchronous call; workers never re-route
`luna_run` returns only after the worker turn finishes, which can take many
minutes for real work; keep waiting (the MCP timeout is 1900 s), do not abandon
it. A task that begins with `[LUNA ROUTER WORKER]` means you ARE the dispatched
worker: do the work directly and never call `luna_run`/`luna_plan` or the CLI.

## Execute, do not simulate
For a concrete work request, invoke **luna_router_v270 / luna_run**, not luna_plan.
Omitting `live` means `true` in both schema AND implementation. Never insert
`live:false`, `--offline`, `--routing local`, `--demo`, or a simulation unless
the user explicitly requests local routing / a demo. Missing credentials, a
catalog problem, or a failed POST is not permission to select offline.

If MCP is not connected, execute the installed CLI with its absolute path:

```powershell
& '{{PYTHON_PATH}}' '{{ROUTER_PATH}}' run --prompt-file '<UTF-8 task file>' --project '<absolute project path>'
```

The installer replaces both placeholders. Do not use an older copy in another
folder. No subcommand on `luna.py` defaults to **run**, never plan.

## Data and fees
The user requested online routing; use `data_policy:"metadata_only"` by default.
Only fixed task classification metadata is sent to Luna (not project contents,
paths, credentials, task text or unapproved summaries). Codex receives the actual
work prompt. `no_external` must be respected; report a conflict rather than
ignoring it. Only read `OPENROUTER_API_KEY` from the process or Windows User/System
environment. Do not inspect credential files or print the key. OpenRouter handles
spend limits; do not add local USD checks. Free workers retain zero-price checks.

## Verify each stage
The engine requires actual Luna evidence before worker dispatch, even when there
is one eligible candidate or a user-pinned worker (Luna still chooses effort).
`luna_status` / `doctor` are not inference. `luna_plan` / `plan` do not run work.
A cache is not a new call; new strict jobs make a fresh POST. Existing genuine
Luna retry jobs preserve provenance and do not repeat the decision each attempt.
Report `decision_source`, `decision_model_called`, `decision_response_id`,
`worker_started`, `worker_completed`, acceptance, and the installed entrypoint.
No response ID means "not supplied", never invent one.
If Luna fails or is rejected, surface status/reason/HTTP code; do not claim success.

Use `luna.py probe` for a single live connection test independent of Codex discovery.
Do not claim Windows/live acceptance from fixture tests.

## Existing behavior preserved
Two independently verified failures at the same model/effort advance one native
effort level, reset the count, and at the native ceiling advance a model tier.
Transport/auth/quota errors are not problem-solving failures. Use `--job-id`,
`--verify-argv-file`, and `--auto-retry` only with a real acceptance command.
`runtime_controller.py` is still an unconnected in-flight switching prototype.
This controls launched workers; it does not change the current ChatGPT UI model.
If `LUNA_ROUTER_WORKER=1`, execute assigned work; do not recursively call the router.
