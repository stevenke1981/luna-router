---
name: openrouter-free-agents
description: Invoke verified free OpenRouter models from Codex through per-model bridge agents, or use native free agents only with an OpenRouter parent provider. Read API key solely from OPENROUTER_API_KEY in the system environment.
---

# OpenRouter Free Agents

1. Detect installed runtime: user scope `$HOME/.codex/free-agent-router/free_agents.py` or project scope `.codex/free-agent-router/free_agents.py`.
2. `python <runtime> list` obtains actual agent IDs and categories. If offline snapshot, instruct user to run `python <release-folder>/free_agents.py sync` then reinstall; don't assume all models are listed.
3. Only send public or explicitly user-approved sanitized content. `call` uses `--data-policy public` or `--data-policy approved --approved`. Never send secrets, access tokens or API keys in generated prompts.
4. For free chat/text use `call --agent <free_model_id> --text-file <path> --data-policy public --output <file>`; preserve returned `requested_model`, `actual_model`, and errors.
5. For Decisions/Embeddings/Rerank/Speech, read `README-FREE-AGENTS.md` and use `--request-file`. Never invoke a non-text model via Codex's model selector.
6. A custom agent TOML cannot change `model_provider`. If parent is ChatGPT, use the bridge or start a child Codex with `codex-run` and approved content. Native TOML only works with the provider already set to OpenRouter.
7. Never claim inference executed based on catalog availability, dry-run or generated agent file; require real API response. Actual execution does not imply independent acceptance tests passed.
8. Keep Luna Decisions and GPT tier/effort escalation separate from free model tool adapter. Don't escalate provider outages/429 by spending credits or silently switching to paid models.
