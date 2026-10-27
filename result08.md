# Direct Luna medium routing delivery

Date: 2026-10-28. Source: `E:\luna-router`; repository: `stevenke1981/luna-router`.

- Native `gpt-6-luna/medium` directly selects `gpt-6.1-sol` or `gpt-6.1-terra`, and low/medium/high/xhigh/max. Live Codex capabilities must support the selected pair.
- Default `luna_decide` records actual native coordinator selection with Host-reported provenance and agent ID; it does not call OpenRouter or claim provider inference. Legacy external selection is explicit `luna_decide_online` / `decide-online`.
- Durable claims, dependencies, ownership, separate integration and actual verifier acceptance remain intact.
- Project/global skill instructions and fresh project defaults updated. Existing conversations do not change models. Restart Codex to load changed MCP schemas.
- Full suite PASS: 330 tests, 313 passed, 17 skipped (18.644 s). One fixture issue with excluded .tmp files was fixed based on two failing installer tests.
- Fresh native workflow PASS: real Luna medium selection, Sol/low worker, configured Sol/high integrator, and independent verifier exit 0, job completed. See `evidence/native-coordinator/summary.json`.
- Global runtime synchronization and file hash readback are recorded in `evidence/native-coordinator/installation.json` after execution. No backup requested; existing state is preserved.

Boundaries: no new live OpenRouter call tested; legacy API behavior has regression tests. Native Host model settings/IDs are observed, but provider wire-model attestation is unavailable. Historical receipts retain their original provenance. No changes to the calculator project are part of this delivery.

Per user request, additional installed MCP smoke and repeated test/review rounds were not run. New MCP schema loading requires restarting Codex.
