#!/usr/bin/env python3
"""Luna Router 2.7: require online Luna, then dispatch; no silent local fallback."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import re
import uuid

import codex_backend
from credentials import resolve_key, key_status
import openrouter_backend as orb
from decision import DECISION_MODEL, assess_decision, build_request, is_decision_model, parse_response
from escalation import RetryStore, hash_evidence_file, summarize, task_fingerprint, validate_job_id
from verifier import read_check, run_check
from ledger import BudgetExceeded, Ledger
from cost_control import describe_fees
from policy import (build_candidates, enforce_selection, fallback_route, normalize_task,
                    routing_state, automatic_metadata_state)

ROOT = Path(__file__).resolve().parent
VERSION = "2.7.0"
EFFORT_ORDER = ["none", "low", "medium", "high", "xhigh", "max"]


def routing_mode(args, config: dict) -> str:
    """Only an explicit CLI opt-out selects local routing.

    Old config.default_mode=local cannot silently disable the installed online
    entrypoint. Both auto and luna REQUIRE a successful Luna decision in the CLI.
    """
    selected = getattr(args, "routing", None) or "luna"
    if getattr(args, "online", False):
        selected = "luna"
    if getattr(args, "offline", False):
        selected = "local"
    if selected not in {"auto", "local", "luna"}:
        raise ValueError("unsupported routing mode")
    return selected


def emit_event(event, **details):
    print(json.dumps({"event": event, "version": VERSION, **details},
                     ensure_ascii=False, allow_nan=False), file=sys.stderr, flush=True)


def attach_receipt(result, args):
    """Persist an inspectable result for every plan/run, including failures."""
    result["version"] = VERSION
    result["entrypoint"] = str(Path(__file__).resolve())
    result["key_status"] = key_status()  # Boolean + source only, NEVER value.
    execution = result.get("execution", {})
    result["worker_started"] = execution.get("worker_started", False) is True
    result["worker_completed"] = execution.get("status") in {"turn_completed", "response_received"}
    receipt = getattr(args, "_receipt_path", None)
    if receipt:
        result["receipt_path"] = str(receipt)
        save_json(receipt, result)
    if getattr(args, "output", None):
        save_json(args.output, result)
    return result


def auto_decision_enabled(mode: str, task: dict, config: dict) -> bool:
    """Auto calls Luna only with an API key and an eligible data policy."""
    if mode == "local" or task["data_policy"] == "no_external":
        return False
    if not resolve_key()[0]:
        return False
    return config.get("decision", {}).get("enabled", False) is True


def _inline_task(args) -> dict:
    """Turn inline or stdin work into locally classified metadata (never LLM)."""
    sources = [bool(getattr(args, "task", None)),
               getattr(args, "prompt", None) is not None,
               bool(getattr(args, "prompt_file", None)),
               getattr(args, "stdin", False)]
    if sum(sources) != 1:
        raise ValueError("Select exactly one of --task, --prompt, --prompt-file or --stdin")
    if getattr(args, "task", None):
        return normalize_task(read_json(args.task))
    if getattr(args, "prompt", None) is not None:
        objective = args.prompt
    elif getattr(args, "prompt_file", None):
        objective = Path(args.prompt_file).read_text(encoding="utf-8-sig")
    else:
        objective = sys.stdin.read(200001)
    if not objective.strip() or len(objective) > 200000 or "\x00" in objective:
        raise ValueError("prompt must contain 1-200000 characters and no NUL")
    lower = objective.lower()
    if re.search(r"\b(architecture|architect|distributed|design system)\b|架構|跨系統|微服務", lower):
        kind, complexity = "architecture", 3
    elif re.search(r"\b(debug|bug|fix|error|exception|crash|failing|regression)\b|錯誤|修復|除錯|異常|閃退|故障", lower):
        kind, complexity = "debugging", 2
    elif re.search(r"\b(code|coding|implement|rust|slint|python|java|refactor|compile|test|blender|godot|unity|typescript|javascript|electron|ffmpeg|automation|agent)\b|程式|實作|編譯|重構|測試|開發|自動化|建模|改造|優化專案", lower):
        kind, complexity = "coding", 2
    elif re.search(r"\b(summarize|summary|translate|readme|documentation)\b|摘要|翻譯|文件|說明", lower):
        kind, complexity = "documentation", 1
    else:
        kind, complexity = "general", 2
    if getattr(args, "kind", None):
        kind = args.kind
    if getattr(args, "complexity", None) is not None:
        complexity = args.complexity
    risk = getattr(args, "risk", None) or ("low" if kind in {"documentation", "summary", "translation"} else "medium")
    needs_tools = not getattr(args, "text_only", False)
    return normalize_task({"objective": objective, "kind": kind,
                           "complexity": complexity, "risk": risk,
                           "needs_tools": needs_tools,
                           "data_policy": getattr(args, "data_policy", None) or "metadata_only",
                           "summary_approved": False})


def _codex_default_plan(task, config, runtime, notes):
    """Explicitly unverified model fallback; only for ordinary unpinned jobs."""
    if (task.get("requested_model") is not None or task["risk"] == "high"
            or task["complexity"] >= 4
            or not config.get("routing", {}).get("allow_codex_cli_default_fallback", True)
            or runtime.get("simulation") is not False
            or runtime.get("discovery_failed") is not True
            or runtime.get("auth_mode") != "chatgpt"):
        return None
    return {"version": VERSION, "status": "planned",
            "decision_source": "codex_cli_default_fallback",
            "simulation": False, "selected_model": None, "backend": "codex_default",
            "requested_effort": None, "effort_note": "codex_cli_default_unverified",
            "reason": "model_list_unavailable_use_authenticated_codex_default",
            "candidate_count": 0,
            "decision_model": config.get("decision", {}).get("model", DECISION_MODEL),
            "decision_metrics": None,
            "decision_cost": {"new_request": False, "reported_cost_usd": None,
                              "local_fee_check": False, "cost_owner": "openrouter",
                              "account_limit_verified": False},
            "decision_call_attempted": False, "decision_response_received": False,
            "decision_model_called": False,
            "validation_status": "not_executed",
            "notes": ["model_and_effort_not_verified", *notes],
            "worker": {"id": "codex/default", "backend": "codex_default",
                       "model_id": None, "efforts": []}}


def read_json(path):
    def reject(value):
        raise ValueError("Non-finite JSON number is not allowed")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON property")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8-sig"), parse_constant=reject, object_pairs_hook=unique)


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def conservative_fallback(task, candidates):
    # Once the decision is uncertain, prefer a predictable GPT family if available.
    known = [c for c in candidates if c["backend"] == "codex"]
    return fallback_route(task, known or candidates)


def make_plan(task, config, runtime, text_catalog, decisions_catalog, online=False, ledger=None, job=None, auto_metadata=False, require_luna=False, fresh_decision=False):
    candidates = build_candidates(task, config, runtime, text_catalog)
    if not candidates and require_luna:
        return {"version": VERSION, "status": "blocked", "stage": "capabilities",
                "reason": "no_verified_worker_candidates", "decision_source": "not_called",
                "decision_model_called": False, "decision_call_attempted": False,
                "decision_response_received": False, "worker_started": False,
                "diagnostic": runtime.get("discovery_error") or "no_eligible_models",
                "next_action": "Run doctor; repair Codex discovery. Use probe to test Luna independently."}
    if not candidates:
        fallback = _codex_default_plan(task, config, runtime, [])
        if fallback is not None:
            return fallback
        return {"version": VERSION, "status": "blocked", "reason": "no_eligible_candidate",
                "requested_model": task.get("requested_model"),
                "diagnostic": runtime.get("discovery_error") or "no_eligible_models"}
    choice = fallback_route(task, candidates)
    source = "local_policy"
    notes = []
    parsed = None
    decision_policy = config.get("decision", {})
    decision_cost = {
        "new_request": False, "reported_cost_usd": None,
        "local_fee_check": False, "cost_owner": "openrouter",
        "account_limit_verified": False,
    }
    attempted = False
    response_received = False
    raw = None
    blocking_reason = None
    if task.get("requested_model") and not require_luna:
        source = "explicit_model"
    elif len(candidates) == 1 and not require_luna:
        source = "single_eligible_candidate"
    elif online and decision_policy.get("enabled") is True:
        try:
            state = routing_state(task)
            if state is None and auto_metadata:
                state = automatic_metadata_state(task)
            if state is None:
                raise ValueError("external_routing_not_allowed")
            # A public catalog outage must not silently bypass the paid decider.
            # In strict mode the exact model is submitted directly; the API is
            # authoritative for availability. No guessed account capabilities.
            decision_model = None if require_luna else orb.get_decision_model(decisions_catalog, decision_policy)
            if decision_model is None and not require_luna:
                raise ValueError("decision_model_unavailable")
            efforts = [e for e in EFFORT_ORDER if any(e in c["efforts"] for c in candidates)]
            body = build_request(state, candidates, efforts)
            fingerprint = {"request": body, "policy": config, "capabilities": candidates,
                           "decision_model": decision_model, "version": VERSION}
            key = hashlib.sha256(json.dumps(fingerprint, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
            raw = ledger.get(key) if ledger and not fresh_decision else None
            cache_hit = raw is not None
            if raw is None:
                if not ledger or not job:
                    raise ValueError("Live inference requires a request counter")
                # Count requests to prevent loops. NO local money reservation or
                # per-request/daily USD limit for OpenRouter-paid Luna Decisions.
                ledger.reserve(job, "decision")
                decision_cost["new_request"] = True
                attempted = True
                emit_event("luna_request_started", model=DECISION_MODEL,
                           endpoint="/api/alpha/decisions")
                raw = orb.decide(body, decision_model, decision_policy)
                response_received = True
                emit_event("luna_response_received", model=raw.get("model"),
                           response_id=raw.get("id"))
                decision_cost["reported_cost_usd"] = orb.reported_cost(raw)
            else:
                decision_cost["cache_hit"] = True
            parsed = parse_response(raw, [c["id"] for c in candidates], efforts)
            if not is_decision_model(parsed["model"], decision_policy["model"]):
                raise ValueError("decision_model_mismatch")
            certain = assess_decision(parsed, task["risk"] == "low") == "accepted"
            # A valid Luna answer is the decision: dispatch immediately instead of
            # blocking for human review. The uncalibrated certainty gate is opt-in.
            if certain or not config.get("policy", {}).get("block_low_certainty", False):
                # Luna owns effort too; the local complexity floor is opt-in.
                choice = enforce_selection(task, candidates, parsed["selected_id"], parsed["effort"],
                                           effort_floor=config.get("policy", {}).get("local_effort_floor", False))
                source = "luna_decisions_cache" if cache_hit else "luna_decisions"
                if not certain:
                    notes.append("luna_low_certainty_accepted")
                if ledger and not cache_hit:
                    ledger.put(key, raw)
            else:
                choice = conservative_fallback(task, candidates)
                source = "local_fallback"
                notes.append("luna_probability_below_uncalibrated_threshold")
                if require_luna:
                    blocking_reason = "luna_decision_below_threshold"
        except BudgetExceeded as exc:
            # Request-count controls are not monetary limits. Never retry a
            # rate-limited paid decision automatically.
            choice = conservative_fallback(task, candidates)
            source = "local_fallback"
            notes.append({"request_limit": str(exc)})
            if require_luna:
                blocking_reason = "decision_request_limit_exceeded"
        except (orb.APIError, ValueError) as exc:
            choice = conservative_fallback(task, candidates)
            source = "local_fallback"
            notes.append(type(exc).__name__)
            if isinstance(exc, orb.APIError):
                notes.append({"http_status": exc.status, "retry_after": exc.retry_after})
            if require_luna:
                blocking_reason = "luna_api_error" if isinstance(exc, orb.APIError) else "invalid_luna_decision"
                # Validation messages are our own literals, never transport bodies.
                if isinstance(exc, ValueError):
                    notes.append({"validation_error": str(exc)})
    elif online:
        notes.append("decision_model_disabled")
    else:
        notes.append("offline_no_model_called")
    if require_luna and source not in {"luna_decisions", "luna_decisions_cache"}:
        emit_event("luna_required_blocked", reason=blocking_reason or "live_luna_required")
        return {"version": VERSION, "status": "blocked", "stage": "decision",
                "reason": blocking_reason or "live_luna_required",
                "decision_source": "luna_decisions_rejected" if parsed else "luna_decisions_error",
                "selected_model": None, "requested_effort": None,
                "decision_model": DECISION_MODEL, "decision_metrics": parsed,
                "decision_call_attempted": attempted,
                "decision_response_received": response_received,
                "decision_model_called": bool(response_received and isinstance(raw, dict) and is_decision_model(raw.get("model"))),
                "decision_response_id": raw.get("id") if isinstance(raw, dict) else None,
                "decision_cost": decision_cost, "notes": notes,
                "validation_status": "not_executed"}
    selected = next(c for c in candidates if c["id"] == choice["selected_id"])
    return {
        "version": VERSION, "status": "planned", "decision_source": source,
        "simulation": bool(runtime.get("simulation") or text_catalog.get("simulation")),
        "selected_model": selected["model_id"], "backend": selected["backend"],
        "requested_effort": choice["effort"] if selected["backend"] == "codex" else None,
        "effort_note": "native_codex_setting_requested" if selected["backend"] == "codex" else "free_worker_native_effort_not_configured",
        "reason": choice.get("reason"), "candidate_count": len(candidates),
        "decision_model": decision_policy.get("model", DECISION_MODEL),
        "decision_metrics": parsed, "decision_cost": decision_cost, "notes": notes,
        "decision_call_attempted": attempted,
        "decision_response_received": response_received,
        "decision_model_called": bool(response_received and isinstance(raw, dict) and is_decision_model(raw.get("model"))),
        "decision_response_id": raw.get("id") if isinstance(raw, dict) else None,
        "require_luna": require_luna,
        "validation_status": "not_executed", "worker": selected,
    }


def collect(args, task):
    """Acquire actual local capabilities and only necessary OpenRouter catalogs."""
    runtime = {"models": [], "simulation": False}
    text_catalog = {"data": []}
    decisions_catalog = {"data": []}
    notes = []
    if args.demo:
        runtime = read_json(ROOT / "examples/runtime-demo.json")
        text_catalog = read_json(ROOT / "evidence/openrouter-text-free.json")
        decisions_catalog = read_json(ROOT / "evidence/openrouter-decisions-luna.json")
        text_catalog["simulation"] = True
        return runtime, text_catalog, decisions_catalog, ["demo_capabilities_are_not_account_entitlement"]
    mode = getattr(args, "_routing_mode", "luna" if getattr(args, "online", False) else "local")
    if getattr(args, "runtime", None) and args.command == "plan" and mode == "local":
        runtime = read_json(args.runtime)
    else:
        if shutil.which("codex"):
            try:
                runtime = codex_backend.discover()
                runtime["_auto_discovered"] = True
                # A successful response also needs positive ChatGPT auth before
                # subscription work is permitted by execute_plan.
                notes.append({"codex_model_list_count": len(runtime.get("models", []))})
            except codex_backend.CodexBackendError as exc:
                code = str(exc)  # sanitized error code, no raw subprocess output
                notes.append({"codex_discovery_error": code})
                auth = codex_backend.login_status().get("auth_mode", "unknown")
                runtime = {"models": [], "simulation": False,
                           "discovery_failed": True, "discovery_error": code,
                           "auth_mode": auth}
                notes.append({"codex_auth_mode": auth})
        else:
            notes.append("codex_cli_not_found")
    if mode == "local":
        if getattr(args, "catalog_file", None) and args.command == "plan":
            text_catalog = read_json(args.catalog_file)
            if isinstance(text_catalog, dict) and "text" in text_catalog:
                text_catalog = text_catalog["text"]
        return runtime, text_catalog, decisions_catalog, notes
    # Public catalog discovery can be online, but no user task text is sent in GETs.
    # Do not ask the network if the key is absent; public GETs alone do not
    # authorize inference and cannot result in an executable free worker.
    if not resolve_key()[0]:
        notes.append("openrouter_key_absent_using_local_policy")
        return runtime, text_catalog, decisions_catalog, notes
    if task["data_policy"] == "no_external":
        notes.append("no_external_task_using_local_policy")
        return runtime, text_catalog, decisions_catalog, notes
    modalities = ("text", "decisions")
    if getattr(args, "_require_luna", False):
        modalities = ("text",) if task["data_policy"] == "public" and not task["needs_tools"] else ()
    for modality in modalities:
        try:
            data = orb.catalog(modality)
            if modality == "text":
                text_catalog = data
            else:
                decisions_catalog = data
        except orb.APIError as exc:
            notes.append({"catalog_error": modality, "status": exc.status})
    return runtime, text_catalog, decisions_catalog, notes


WORKER_HEADER = (
    "[LUNA ROUTER WORKER] Luna Router already chose your model and reasoning effort. "
    "You are the dispatched worker: do the task below directly. Do not call luna_run, "
    "luna_plan or any Luna Router tool/CLI, and do not wait for routing approval.\n\n"
)


def execute_plan(plan, task, runtime, args, ledger, job):
    if plan["status"] != "planned":
        return plan
    if plan.get("simulation"):
        return {"status": "blocked", "reason": "simulation_cannot_execute"}
    if getattr(args, "_require_luna", False) and plan.get("decision_source") not in {
        "luna_decisions", "luna_decisions_cache", "retry_state"
    }:
        return {"status": "blocked", "reason": "live_luna_required", "worker_started": False}
    worker = plan["worker"]
    emit_event("worker_dispatch", backend=worker["backend"],
               requested_model=worker["model_id"], requested_effort=plan.get("requested_effort"))
    if worker["backend"] == "codex":
        return codex_backend.run_task(
            WORKER_HEADER + task["objective"], worker["model_id"], plan["requested_effort"],
            Path(args.project).resolve(), runtime, sandbox=args.sandbox,
            use_recent_discovery=bool(runtime.get("_auto_discovered", False)),
        )
    if worker["backend"] == "codex_default":
        return codex_backend.run_default_task(
            WORKER_HEADER + task["objective"], Path(args.project).resolve(), runtime, sandbox=args.sandbox,
        )
    if task["data_policy"] != "public" or task["needs_tools"]:
        return {"status": "blocked", "reason": "free_text_worker_not_eligible"}
    # Refresh once at execution, so a stale plan cannot silently use a paid model.
    fresh = orb.get_free_models(orb.catalog("text"))
    model = next((m for m in fresh if m["id"] == worker["model_id"]), None)
    if model is None:
        return {"status": "blocked", "reason": "free_model_no_longer_available"}
    if ledger is None:
        return {"status": "blocked", "reason": "free_worker_requires_request_counter"}
    ledger.reserve(job, "free_worker")
    return orb.complete(model, task["objective"], max_tokens=task["output_tokens"])



def escalation_candidates(task, config, runtime, text_catalog):
    """Allow compatible legacy tiers for *upward* escalation only.

    Selection at the start of a job still respects normal modern-model
    preferences; exact model pins and all task hard constraints remain in force.
    """
    extended = copy.deepcopy(config)
    extended.setdefault("policy", {})["allow_legacy_auto"] = True
    return build_candidates(task, extended, runtime, text_catalog)


def resume_plan(state, task, config, runtime, text_catalog):
    """Resume a saved, explicitly verified route; never re-call paid decider."""
    if state["status"] != "ready":
        return {"version": VERSION, "status": "blocked", "reason": "retry_job_" + state["status"]}
    candidates = escalation_candidates(task, config, runtime, text_catalog)
    route = state["route"]
    matches = [candidate for candidate in candidates if candidate["model_id"] == route["model"]]
    if len(matches) != 1:
        return {"version": VERSION, "status": "blocked", "reason": "saved_model_unavailable_or_ineligible"}
    worker = matches[0]
    if worker["backend"] != route["backend"]:
        return {"version": VERSION, "status": "blocked", "reason": "saved_backend_changed"}
    effort = route["effort"] if worker["backend"] == "codex" else "low"
    # The saved route was validated when registered (possibly a Luna effort below
    # the local floor); only re-check model support here.
    verified = enforce_selection(task, [worker], route["model"], effort, effort_floor=False)
    if worker["backend"] == "codex" and verified["effort"] != effort:
        return {"version": VERSION, "status": "blocked", "reason": "saved_effort_no_longer_supported"}
    return {
        "version": VERSION, "status": "planned", "decision_source": "retry_state",
        "simulation": bool(runtime.get("simulation") or text_catalog.get("simulation")),
        "selected_model": worker["model_id"], "backend": worker["backend"],
        "requested_effort": route["effort"],
        "effort_note": "native_codex_setting_requested" if worker["backend"] == "codex" else "free_worker_native_effort_not_configured",
        "reason": state["last_transition"], "candidate_count": len(candidates),
        "decision_model": config.get("decision", {}).get("model", DECISION_MODEL),
        "decision_metrics": None,
        "decision_cost": {"new_request": False, "reported_cost_usd": None,
                          "local_fee_check": False, "cost_owner": "openrouter", "account_limit_verified": False},
        "decision_call_attempted": False, "decision_response_received": False,
        "decision_model_called": False, "notes": ["no_new_luna_request_on_retry"],
        "validation_status": "not_executed", "worker": worker,
    }


def _execution_outcome(execution, check, project):
    """Only an actual checker or explicitly supplied feedback can certify fail/pass."""
    if execution.get("status") not in ("turn_completed", "response_received"):
        status = execution.get("status")
        return {"outcome": "unknown" if status == "unknown" else "blocked",
                "evidence_kind": "worker", "reason": execution.get("error", "worker_not_completed")}
    if check is None:
        return {"outcome": "unverified", "evidence_kind": "worker", "reason": "acceptance_checker_not_configured"}
    return run_check(check, project)


def retry_run(args, task, config, runtime, text_catalog, decision_catalog, ledger, job, notes):
    """Perform up to N attempts. Escalation requires proven acceptance failures.

    Every new worker attempt still re-discovers Codex entitlement; saved state
    cannot be used as account permission. No automatic retry on unknown results.
    """
    project = Path(args.project).resolve()
    fingerprint = task_fingerprint(task, project)
    store = RetryStore(Path(args.state_dir) / "escalation.sqlite")
    state = store.get(args.job_id)
    if state is not None and state["fingerprint"] != fingerprint:
        raise ValueError("job-id belongs to another task or project")
    if state is not None and getattr(args, "_require_luna", False) and not state.get("luna_origin_verified"):
        raise ValueError("saved_job_has_no_luna_evidence_use_new_job_id_or_explicit_offline")
    check = read_check(args.verify_argv_file, config.get("escalation", {}).get("verifier_timeout_seconds", 300)) if args.verify_argv_file else None
    if args.auto_retry and check is None:
        raise ValueError("--auto-retry requires --verify-argv-file (independent test command)")
    maximum = args.max_attempts if args.auto_retry else 1
    if maximum < 1 or maximum > 100:
        raise ValueError("--max-attempts must be 1-100")
    if maximum > config.get("escalation", {}).get("max_auto_attempts", 20):
        raise ValueError("--max-attempts exceeds configured max_auto_attempts")
    if state is None:
        plan = make_plan(task, config, runtime, text_catalog, decision_catalog,
                         online=getattr(args, "_decision_online", False), ledger=ledger, job=job,
                         auto_metadata=getattr(args, "_auto_metadata", False),
                         require_luna=getattr(args, "_require_luna", False),
                         fresh_decision=getattr(args, "_require_luna", False))
        plan.setdefault("notes", []).extend(notes)
        if plan.get("backend") == "codex_default":
            return {"plan": {"status": "blocked", "reason": "default_codex_fallback_cannot_auto_escalate",
                             "notes": plan.get("notes", [])},
                    "retry": {"status": "blocked", "reason": "model_list_needed_for_escalation"}}
        if plan["status"] != "planned":
            return {"plan": plan, "retry": {"status": "blocked", "reason": plan.get("reason")}}
        state = store.register(args.job_id, fingerprint, plan, escalation_candidates(task, config, runtime, text_catalog))
    else:
        plan = resume_plan(state, task, config, runtime, text_catalog)
        plan.setdefault("notes", []).extend(notes)
    if plan["status"] != "planned":
        return {"plan": plan, "retry": summarize(state)}
    if plan.get("simulation"):
        return {"plan": {"status": "blocked", "reason": "simulation_cannot_execute"}, "retry": summarize(state)}
    first_plan = {k: v for k, v in plan.items() if k != "worker"}
    history = []
    last_execution = {"status": "not_executed"}
    for index in range(maximum):
        if index:
            state = store.get(args.job_id)
            plan = resume_plan(state, task, config, runtime, text_catalog)
            if plan["status"] != "planned":
                break
        attempt_id, saved = store.begin_attempt(args.job_id, fingerprint)
        # Dispatch must agree with atomically persisted route.
        if (plan["selected_model"], plan["backend"], plan["requested_effort"]) != (
            saved["model"], saved["backend"], saved["effort"]
        ):
            raise ValueError("attempt route changed unexpectedly")
        try:
            worker_task = dict(task)
            if state["attempts"] > 0:
                # Feed only authored QA guidance, not raw verifier output or
                # untrusted external text. Never re-send failure evidence to
                # Luna Decisions; its initial approval remains unchanged.
                worker_task["objective"] += (
                    "\n\n[QA retry context] The previous worker turn did not pass independent "
                    "acceptance tests. Re-examine the current project, reproduce the failing tests, "
                    "locate the root cause, and verify a different or corrected approach. "
                    "Do not claim success without acceptance evidence."
                )
            last_execution = execute_plan(plan, worker_task, runtime, args, ledger, job)
            evidence = _execution_outcome(last_execution, check, project)
        except (ValueError, OSError, sqlite3.Error, orb.APIError, BudgetExceeded, codex_backend.CodexBackendError) as exc:
            # An uncertain dispatched request must never be retried blindly.
            last_execution = {"status": "unknown", "reason": type(exc).__name__}
            evidence = {"outcome": "unknown", "evidence_kind": "worker", "reason": "worker_request_uncertain"}
        state = store.record_outcome(
            args.job_id, attempt_id, evidence["outcome"], task, fingerprint=fingerprint,
            evidence_kind=evidence["evidence_kind"], evidence_sha256=evidence.get("evidence_sha256"),
            detail={key: value for key, value in evidence.items() if key not in {"outcome", "evidence_kind", "evidence_sha256"}},
        )
        history.append({"attempt_id": attempt_id, "model": saved["model"],
                        "effort": saved["effort"], "outcome": evidence["outcome"],
                        "transition": state["last_transition"]})
        if state["status"] != "ready":
            break
        if not args.auto_retry:
            break
    final_plan = plan.copy()
    final_plan.pop("worker", None)
    return {
        "plan": final_plan, "initial_plan": first_plan, "execution": last_execution,
        "acceptance": "passed" if state["status"] == "completed" else "not_independently_verified_or_failed",
        "retry": summarize(state), "attempt_history": history,
        "auto_retry_limit_reached": bool(args.auto_retry and len(history) == maximum and state["status"] == "ready"),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo", help="Show offline routes; no keys or network")
    cat = sub.add_parser("catalog", help="Refresh free text workers and the selected Luna decision model")
    cat.add_argument("--output", default=str(ROOT / ".state/catalog.json"))
    cat.add_argument("--config", default=str(ROOT / "config.json"))
    sub.add_parser("doctor", help="Check Auto Routing, Codex auth and model/list without inference")
    dis = sub.add_parser("discover", help="Read the local Codex model/list; no inference")
    dis.add_argument("--output", default=str(ROOT / ".state/runtime.json"))
    sub.add_parser("quota", help="Read only current OpenRouter free request quota")
    sub.add_parser("spend-status", help="Read OpenRouter current API-key spending cap (read-only, no inference)")
    budget = sub.add_parser("budget", help="Compatibility alias for fee-policy; OpenRouter costs are provider-managed")
    budget.add_argument("--config", default=str(ROOT / "config.json"))
    fees = sub.add_parser("fee-policy", help="Show which backend enforces a local fee policy; no inference")
    fees.add_argument("--config", default=str(ROOT / "config.json"))
    stat = sub.add_parser("retry-status", help="Show a named job's verified retry/escalation state")
    stat.add_argument("--job-id", required=True)
    stat.add_argument("--state-dir", default=str(ROOT / ".state"))
    feedback = sub.add_parser("feedback", help="Resolve an unverified completed attempt with user-supplied evidence")
    feedback.add_argument("--job-id", required=True)
    feedback.add_argument("--state-dir", default=str(ROOT / ".state"))
    feedback.add_argument("--task", required=True)
    feedback.add_argument("--project", default=".")
    feedback.add_argument("--result", choices=["passed", "failed"], required=True)
    feedback.add_argument("--evidence-file", required=True)
    feedback.add_argument("--attempt-id", help="Defaults to latest attempt")
    for name in ("plan", "run"):
        p = sub.add_parser(name)
        p.add_argument("--task", help="Task JSON file; choose exactly one task input")
        p.add_argument("--prompt", help="Inline task text; avoid secrets in command-line arguments")
        p.add_argument("--prompt-file", help="UTF-8 plaintext task file")
        p.add_argument("--stdin", action="store_true", help="Read task prompt from stdin")
        p.add_argument("--kind", choices=["coding","debugging","architecture","research","documentation","general","summary","translation","extraction","classification","visual_qa"], help="Override local prompt classification")
        p.add_argument("--complexity", type=int, choices=[1,2,3,4], help="Override task complexity")
        p.add_argument("--risk", choices=["low","medium","high"], help="Override task risk")
        p.add_argument("--text-only", action="store_true", help="Inline prompt needs no local tools")
        p.add_argument("--data-policy", choices=["metadata_only","public","no_external"],
                       help="Inline prompt data sharing; public permits sending full text to OpenRouter")
        p.add_argument("--routing", choices=["auto","luna","local"], help="Default luna; auto also requires Luna. local is an explicit offline opt-out")
        p.add_argument("--offline", action="store_true", help="No task info sent to OpenRouter; local routing only")
        p.add_argument("--config", default=str(ROOT / "config.json"))
        p.add_argument("--output", help="Save a JSON report")
        p.add_argument("--runtime", help="Offline discovered Codex runtime JSON")
        p.add_argument("--catalog-file", help="Offline free text catalog JSON")
        p.add_argument("--online", action="store_true", help="Explicit alias for default required-online Luna routing")
        p.add_argument("--demo", action="store_true", help="Use simulated capabilities; cannot execute")
        p.add_argument("--project", default=".")
        p.add_argument("--sandbox", choices=["read-only", "workspace-write"], default="workspace-write")
        p.add_argument("--job-id", help="Persist retry state under this job ID; reusing requires same task/project")
        p.add_argument("--state-dir", default=str(ROOT / ".state"))
        if name == "run":
            p.add_argument("--verify-argv-file", help="JSON verifier argv; zero exit = pass, nonzero = verified failure")
            p.add_argument("--auto-retry", action="store_true", help="Retry after independent verifier failures (requires --job-id and verifier)")
            p.add_argument("--max-attempts", type=int, default=20, help="Max Codex/free attempts per invocation when auto-retrying")
    args = parser.parse_args(argv)
    try:
        if args.command == "retry-status":
            store = RetryStore(Path(args.state_dir) / "escalation.sqlite")
            state = store.get(args.job_id)
            print(json.dumps({"retry": summarize(state), "last_attempt": store.latest_attempt(args.job_id)}, ensure_ascii=False, indent=2))
            return 0 if state is not None else 2
        if args.command == "feedback":
            store = RetryStore(Path(args.state_dir) / "escalation.sqlite")
            task = normalize_task(read_json(args.task))
            state = store.get(args.job_id)
            if state is None:
                raise ValueError("unknown job-id")
            evidence_hash = hash_evidence_file(args.evidence_file)
            last = store.latest_attempt(args.job_id)
            if last is None:
                raise ValueError("job has no prior worker attempt")
            chosen_attempt = args.attempt_id or last["attempt_id"]
            result = store.record_outcome(
                args.job_id, chosen_attempt, args.result, task,
                fingerprint=task_fingerprint(task, Path(args.project)),
                evidence_kind="manual", evidence_sha256=evidence_hash,
                detail={"source": "manual_user_supplied_qa_report"}, from_review=True,
            )
            print(json.dumps({"retry": summarize(result), "attempt_id": chosen_attempt, "evidence_sha256": evidence_hash}, ensure_ascii=False, indent=2))
            return 0
        if args.command == "catalog":
            config = read_json(args.config)
            result = {modality: orb.catalog(modality) for modality in ("text", "decisions")}
            result["text"]["data"] = orb.get_free_models(result["text"], "text")
            selected = orb.get_decision_model(result["decisions"], config["decision"])
            result["decisions"]["data"] = [selected] if selected else []
            save_json(args.output, result)
            print(json.dumps({"saved": args.output, "free_text_count": len(result["text"]["data"]),
                              "decision_model": selected["id"] if selected else None,
                              "decision_pricing_per_token": selected["pricing"] if selected else None}, ensure_ascii=False))
            return 0
        if args.command == "doctor":
            config = read_json(ROOT / "config.json")
            report = {"version": VERSION, "routing_default": config.get("routing", {}).get("default_mode"),
                      "openrouter_key_present": key_status()["present"],
                      "openrouter_key_source": key_status()["source"],
                      "entrypoint": str(Path(__file__).resolve()),
                      "online_required_by_default": True,
                      "codex_found": bool(shutil.which("codex")),
                      "codex_auth_mode": "unknown", "model_list": "not_checked",
                      "model_count": None, "discovery_error": None,
                      "default_cli_fallback_possible": False, "no_model_inference_performed": True}
            if report["codex_found"]:
                report["codex_auth_mode"] = codex_backend.login_status().get("auth_mode", "unknown")
                try:
                    found = codex_backend.discover()
                    report["model_list"] = "ok"
                    report["model_count"] = len(found.get("models", []))
                    report["codex_auth_mode"] = found.get("auth_mode", "unknown")
                except codex_backend.CodexBackendError as exc:
                    report["model_list"] = "failed"
                    report["discovery_error"] = str(exc)
                    report["default_cli_fallback_possible"] = report["codex_auth_mode"] == "chatgpt"
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        if args.command == "discover":
            result = codex_backend.discover()
            save_json(args.output, result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "quota":
            print(json.dumps(orb.get_quota(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "spend-status":
            print(json.dumps(orb.get_spend_status(), ensure_ascii=False, indent=2))
            return 0
        if args.command in ("budget", "fee-policy"):
            config = read_json(args.config)
            print(json.dumps(describe_fees(config), ensure_ascii=False, indent=2))
            return 0
        if args.command == "demo":
            config = read_json(ROOT / "config.json")
            runtime = read_json(ROOT / "examples/runtime-demo.json")
            text_catalog = read_json(ROOT / "evidence/openrouter-text-free.json")
            text_catalog["simulation"] = True
            output = []
            for path in sorted((ROOT / "examples").glob("task-*.json")):
                task = normalize_task(read_json(path))
                plan = make_plan(task, config, runtime, text_catalog, {"data": []})
                plan.pop("worker", None)
                output.append({"example": path.name, **plan})
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return 0
        if args.command == "run" and args.auto_retry and not args.job_id:
            raise ValueError("--auto-retry requires --job-id for durable escalation history")
        if args.command == "run" and args.verify_argv_file and not args.job_id:
            raise ValueError("--verify-argv-file requires --job-id")
        if args.demo and args.command == "run":
            raise ValueError("Demo capabilities cannot be used for live execution")
        if args.offline and (args.online or args.routing == "luna"):
            raise ValueError("--offline cannot be combined with --online / --routing luna")
        if args.online and args.routing == "local":
            raise ValueError("--online cannot be combined with --routing local")
        args._receipt_path = Path(args.state_dir).resolve() / "receipts" / (uuid.uuid4().hex + ".json")
        if os.environ.get("LUNA_ROUTER_WORKER") == "1":
            raise ValueError("recursive_router_invocation_blocked")
        task = _inline_task(args)
        config = read_json(args.config)
        args._routing_mode = routing_mode(args, config)
        args._require_luna = args._routing_mode != "local" and not args.demo
        if args._require_luna:
            if not resolve_key()[0]:
                raise ValueError("openrouter_key_missing_online_required")
            if task["data_policy"] == "no_external":
                raise ValueError("no_external_conflicts_with_online_use_explicit_offline")
            if config.get("decision", {}).get("enabled") is not True:
                raise ValueError("luna_disabled_online_required")
            if config.get("decision", {}).get("model") != DECISION_MODEL:
                raise ValueError("unexpected_decision_model")
        if args.command == "run" and not Path(args.project).resolve().is_dir():
            raise ValueError("project_directory_not_found")
        emit_event("routing_started", mode=args._routing_mode, required_online=args._require_luna,
                   entrypoint=str(Path(__file__).resolve()), key_source=key_status()["source"])
        args._decision_online = auto_decision_enabled(args._routing_mode, task, config)
        args._auto_metadata = (args._routing_mode != "local"
                               and config.get("routing", {}).get("automatic_metadata_decisions", True))
        if args.demo:
            args._decision_online = False
        runtime, text_catalog, decision_catalog, notes = collect(args, task)
        notes.append({"routing_mode": args._routing_mode,
                      "remote_decision_enabled": args._decision_online})
        job = uuid.uuid4().hex
        online = args._decision_online
        # Ledger is also used for OpenRouter free worker request-count guardrails.
        # Local-only Codex work needs no database or API key.
        use_ledger = args._decision_online or (args._routing_mode != "local" and bool(resolve_key()[0]))
        ledger = Ledger(Path(args.state_dir) / "usage.sqlite", config["policy"]) if use_ledger else None
        if args.command == "run" and args.job_id:
            result = retry_run(args, task, config, runtime, text_catalog, decision_catalog, ledger, job, notes)
            attach_receipt(result, args)
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
            status = result.get("retry", {}).get("status")
            if status == "completed":
                return 0
            if result.get("plan", {}).get("status") == "blocked":
                return 2
            if status == "needs_verification":
                return 4
            if status in ("exhausted", "paused") or result.get("auto_retry_limit_reached"):
                return 3
            if result.get("attempt_history") and result["attempt_history"][-1]["outcome"] == "failed":
                return 3
            return 0
        if args.command == "plan" and args.job_id:
            existing = RetryStore(Path(args.state_dir) / "escalation.sqlite").get(args.job_id)
            if existing is not None:
                if existing["fingerprint"] != task_fingerprint(task, Path(args.project)):
                    raise ValueError("job-id belongs to another task or project")
                if args._require_luna and not existing.get("luna_origin_verified"):
                    raise ValueError("saved_job_has_no_luna_evidence_use_new_job_id_or_explicit_offline")
                plan = resume_plan(existing, task, config, runtime, text_catalog)
            else:
                plan = make_plan(task, config, runtime, text_catalog, decision_catalog, online, ledger, job,
                                 auto_metadata=args._auto_metadata, require_luna=args._require_luna,
                                 fresh_decision=args._require_luna)
        else:
            plan = make_plan(task, config, runtime, text_catalog, decision_catalog, online, ledger, job,
                             auto_metadata=args._auto_metadata, require_luna=args._require_luna,
                                 fresh_decision=args._require_luna)
        plan.setdefault("notes", []).extend(notes)
        result = {"plan": plan, "routing_mode": args._routing_mode}
        if args.command == "run":
            result["execution"] = execute_plan(plan, task, runtime, args, ledger, job)
            result["acceptance"] = "not_independently_verified"
            result["model_switch_verified"] = False  # Actual wire model not observed by Codex CLI JSONL
        plan.pop("worker", None)
        attach_receipt(result, args)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        if plan["status"] == "blocked":
            return 2
        if "execution" in result and result["execution"].get("status") not in ("response_received", "turn_completed"):
            return 3
        return 0
    except (ValueError, OSError, sqlite3.Error, orb.APIError, BudgetExceeded, codex_backend.CodexBackendError) as exc:
        # Never emit raw transport response bodies or environment values.
        failure = {"status": "blocked", "error_type": type(exc).__name__, "message": str(exc),
                   "decision_source": "not_called", "worker_started": False, "worker_completed": False}
        if "plan" in locals():
            failure["plan"] = {k:v for k,v in plan.items() if k != "worker"}
            failure.pop("decision_source", None)
        if isinstance(exc, orb.APIError):
            failure["http_status"] = exc.status
        if getattr(args, "command", None) in {"plan", "run"}:
            try:
                attach_receipt(failure, args)
            except (OSError, ValueError):
                failure["receipt_write_failed"] = True
        print(json.dumps(failure, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
