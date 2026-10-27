# Visible Luna workflow implementation plan

User goal: a configured Luna coordinator decomposes work, real Luna Decisions selects each worker model/effort, native visible subagents execute in parallel, and configurable GPT-6.1 Sol/high integrates and verifies.

1. Replace the stop-all-on-blocked instruction in active global/project/installed skill sources; preserve truthful failure and sandbox boundaries.
2. Implement a durable workflow store with project binding, task dependencies, disjoint write ownership, bounded parallel dispatch, native agent IDs, incremental events, integration and independent verification gates.
3. Add quick decision-only CLI/MCP entrypoints, bounded transient decision retries and host dispatch descriptors. Keep legacy synchronous run compatibility.
4. Document and demonstrate native host dispatch with a real Luna coordinator, native workers when Decisions returns a valid route, and configured Sol/high integration.
5. Run focused tests, one independent integration review, live decision/native-agent checks, manifest/install checks and deliver concrete evidence.

Ownership: coordinator worktree owns SPEC.md and docs/WORKFLOW.md; Sol/high engine worktree owns workflow.py and tests/test_workflow.py; parent owns adapters, decision retry module, instruction installation, integration and release evidence. Each author works in a separate worktree; merge only assigned files.

Acceptance: API failure never stops independent work; no invented decisions or model switches; quick decide never starts worker; native IDs/actions visible; dependencies and overlap enforced; integration defaults configurable; completion requires independent verifier evidence; data_policy metadata_only; legacy tests pass; live and fixture evidence separated.
