# Native coordinator acceptance — 2026-10-28

The native gpt-6-luna/medium coordinator selected Sol/high and Terra/high for implementation, then Sol/low for a deterministic live smoke. Host supplied actual native agent IDs. The new CLI registered the coordinator selection, claimed dispatch, recorded worker completion and a separate configured Sol/high integrator, and executed an independent verifier. The workflow completed with exit 0.

`summary.json` contains observed roles, dispatch identity, plan provenance and verifier output hash. No OpenRouter decision was used for this native workflow. Provider wire-model attestation is not available; Host identity/configuration is not a fabricated API response.

Full suite: 330 tests, 313 passed, 17 skipped. The first run had two installer-fixture errors because fixture manifests included `.tmp` while the installer excluded it. Aligning fixture exclusions fixed those errors; the final complete run passed. Skipped tests do not imply live acceptance.

Implementation workers ran before the new registration feature was installed, using the coordinator's actual Host response. They are not retroactively represented as dispatched workflow tasks. The subsequent fresh smoke used the new durable protocol end to end.
