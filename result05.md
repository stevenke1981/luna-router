# result05 — scoped worker repair

Owned files: tests/test_required_online.py, examples/live-smoke.py, tests/test_live_smoke.py, README.md, result05.md.

- Fixed host MCP fixtures inheriting LUNA_ROUTER_WORKER=1. The explicit recursion-guard test still sets the marker and passes; production code is unchanged.
- Fixed installer copy recursion when TEMP/TMP is beneath ROOT by excluding the ancestor containing the active fixture temp tree. Installer subprocess also receives a host fixture environment without the worker marker. Unix HTTP fixture child environment is likewise explicit.
- Added host-only real run smoke: fresh isolated project, random inputs and nonce, metadata_only, workspace-write, no security changes. Four JSON layers and exact host-side artifact verification determine exit status. Receipt/artifact/summary are preserved when available. Missing/malformed output and subprocess errors fail closed; raw subprocess output is not printed.
- README documents invocation, evidence boundaries and network-restricted worker limitation.

Validation (fixture evidence only):
- Under LUNA_ROUTER_WORKER=1 and TEMP/TMP=E:\luna-router-v2.7.0\.tmp, test_required_online.py: final 29 tests, OK, 14 Unix-only skips (15 passed). Initial run exposed one additional installer child marker inheritance failure; narrowly fixed it and reran only the affected test file.
- test_live_smoke.py: 4 tests OK. Fake subprocess only, including missing/stale/wrong artifact, local decision, missing decision ID, unstarted worker, failed turn, blocked receipt, nonzero exit and recursive marker rejection.
- Final git diff unavailable: directory is not a Git repository (git reported “Not a git repository”). Scoped file inspection used instead.

NOT_RUN: live harness / router invocation, live API, full suite, package manifest, installation acceptance, commit, push and remote checks. Parent owns these. No API changes were made; supplied parent live evidence was not independently rerun here.
