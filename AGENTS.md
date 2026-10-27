# Luna Router native coordinator workflow

- Read `.agents/skills/luna-workflow/SKILL.md` before project work. The default coordinator is `gpt-6-luna/medium`; it directly selects each worker's model and effort.
- Worker choices are exactly `gpt-6.1-sol` or `gpt-6.1-terra`, with `low`, `medium`, `high`, `xhigh`, or `max`, intersected with live Codex capabilities. Do not silently replace an unavailable choice.
- Prefer `gpt-6.1-terra` for computer use (desktop/browser/GUI operation); prefer `gpt-6.1-sol` for coding, debugging, code analysis and difficult problems. Split mixed tasks when useful; Luna still chooses effort.
- Use a visible native coordinator when the Host is not already Luna/medium. Do not recursively create coordinators. Preserve its real Host agent ID and actual selection.
- Start a workflow, record the coordinator's selection through `luna_decide`, claim dispatch, then use native `spawn_agent` with the returned model/effort and record its real ID. MCP records Host evidence; it does not run a decision model or attest its wire identity.
- This default workflow does not use OpenRouter, an API key, local-policy heuristics, or the old Decisions API. `luna_decide_online` / CLI `decide-online` and synchronous `luna_run` are explicit legacy compatibility paths, not substitutes for direct coordinator selection.
- Configurable integration defaults to `gpt-6.1-sol/high`. Execute independent acceptance through `luna_workflow_verify`; a finished worker or integrator is not acceptance.
- Keep write scopes disjoint. Do not replay uncertain claims, spawns, or verifier executions. Continue independent work if one route is blocked; never invent a successful route.
- Tasks starting with `[LUNA ROUTER WORKER]` or `LUNA_ROUTER_WORKER=1` execute assigned work directly and never recursively call the router.
- Never print credentials. Legacy OpenRouter calls require the existing data boundary; metadata_only sends fixed classifications only.
- Report native coordinator selection, dispatch/agent IDs, completion, integration and independent checks separately. Configuration affects newly launched roles, not the current conversation model.
