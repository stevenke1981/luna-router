# v2.4.0 Step-boundary runtime policy prototype

Reference: https://x.com/miu21590/status/2101857866378362926/video/1
Secondary account: https://jevforagents.com/builds/dynamic-reasoning-effort

The original X video could not be directly inspected in this environment; this is a grounded interpretation of its publicly described use-case, **not** a clone of unreleased Jev implementation.

## Deliverable / boundaries

- Adds `runtime_controller.py`, deterministic pure transition function to recommend **next-step** effort at safe checkpoints.
- Proactive upshift on evidenced complex step, reversible downshift after two evidenced routine steps, 2 verified failures per upshift; at native max emits `escalate_model_required_at_native_ceiling` signal for integration with existing `escalation.advance_route()`.
- Does not treat transport/rate-limit/auth/tool problems as implementation failures.
- Respects supported native effort levels and configured floor.
- No API calls made, no automatic model changes to ChatGPT or Codex.
- Current `router.py` remains v2.3 execution behavior; this module is *not wired into Codex live step events* because validated incremental event hooks and cache retention guarantees are not available from the supplied recording. Do not claim this is a working in-flight switch.
- Model changes must be initiated by a runner at next supported session/turn boundary; preserve an explicit checkpoint summary when continuing to a new worker and record actual effective model/effort.

## Real integration remaining

1. Read native Codex event stream and classify completed work steps with actual verifier evidence.
2. Implement safe pause/resume or new-turn handoff; no speculative interruption of model inference.
3. If supported, apply new effort on the *next* turn and verify applied model/effort in native events.
4. Measure cache hit/miss before and after changes; no unverified prompt caching claims.
5. Enforce user authorization for external OpenRouter metadata-only task summaries and keep provider-side billing controls.
6. Use separate metrics for proposal, actual application, verification, cost, and cache observations.

Run `python runtime-demo.py` for deterministic simulated checkpoints and `python -m unittest discover -s tests -v` for tests.
