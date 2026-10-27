"""Local routing constraints; no network calls and no model execution.

Catalog presence is not account entitlement. GPT candidates are drawn only from
the caller's runtime inventory. Simulation inventories remain simulations; the
host must label them and must not use them to claim an actual model switch.
"""

from __future__ import annotations

import copy
import json
from typing import Any


TASK_KINDS = frozenset({
    "general", "summary", "translation", "extraction", "classification",
    "documentation", "coding", "debugging", "architecture", "research", "visual_qa",
})
TOOL_KINDS = frozenset({"coding", "debugging", "architecture"})
SOL_KINDS = frozenset({"coding", "debugging", "architecture", "research"})
FAMILIES = ("luna", "terra", "sol", "astra")
EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")
MAX_ROUTING_STATE_BYTES = 24 * 1024


def _text(value: Any, label: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError(f"{label} must be a {'possibly empty' if empty else 'non-empty'} string")
    return value


def _integer(value: Any, label: str, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} must be <= {maximum}")
    return value


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a bool")
    return value


def _enum(value: Any, allowed: Any, label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{label} is not a supported value")
    return value


def normalize_task(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate the task and retain only the supported schema fields.

    Default privacy is metadata-only with no approved external summary. The
    objective is preserved for the selected executor and is not silently trimmed.
    """
    if not isinstance(raw, dict):
        raise ValueError("task must be an object")
    kind = _enum(raw.get("kind", "general"), TASK_KINDS, "kind")
    result = {
        "objective": _text(raw.get("objective"), "objective"),
        "summary": _text(raw.get("summary", ""), "summary", empty=True),
        "kind": kind,
        "complexity": _integer(raw.get("complexity", 2), "complexity", 1, 4),
        "risk": _enum(raw.get("risk", "medium"), ("low", "medium", "high"), "risk"),
        "needs_tools": _boolean(raw.get("needs_tools", kind in TOOL_KINDS), "needs_tools"),
        "input_tokens": _integer(raw.get("input_tokens", 0), "input_tokens", 0),
        "output_tokens": _integer(raw.get("output_tokens", 2048), "output_tokens", 1, 8192),
        "data_policy": _enum(raw.get("data_policy", "metadata_only"), ("public", "metadata_only", "no_external"), "data_policy"),
        "summary_approved": _boolean(raw.get("summary_approved", False), "summary_approved"),
        "implementation_failures": _integer(raw.get("implementation_failures", 0), "implementation_failures", 0, 5),
        "failure_kind": _enum(raw.get("failure_kind", "none"), ("none", "implementation", "environment", "quota", "permission"), "failure_kind"),
    }
    for field in ("requested_model", "compatibility_model"):
        value = raw.get(field)
        result[field] = None if value is None else _text(value, field)
    return result


def _effort_floor(task: dict[str, Any]) -> str:
    # Historical implementation_failures is informational metadata, NOT
    # independent evidence. The persisted retry engine requires *two verified*
    # failures before changing native effort or worker tier.
    return EFFORTS[task["complexity"]]


def _family_allowed(task: dict[str, Any], family: str) -> bool:
    if task["risk"] == "high" or task["complexity"] == 4:
        return family == "astra"
    if task["complexity"] == 3:
        return family in ("sol", "astra")
    return family in FAMILIES or family == "free"


def _free_task_allowed(task: dict[str, Any]) -> bool:
    return (
        task["risk"] == "low"
        and task["complexity"] <= 2
        and not task["needs_tools"]
        and task["data_policy"] == "public"
        and task["kind"] != "visual_qa"
    )


def _free_context_fits(task: dict[str, Any], record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    context = record.get("context_length")
    if isinstance(context, bool) or not isinstance(context, int) or context <= 0:
        return False
    try:
        actual_input_bound = len(task["objective"].encode("utf-8"))
    except UnicodeEncodeError:
        return False
    # Match the text backend's conservative framing/output budget and also
    # respect a larger input estimate supplied by the caller.
    required = max(task["input_tokens"], actual_input_bound) + 2048 + 2 * task["output_tokens"]
    if required * 5 > context * 4:
        return False
    top = record.get("top_provider")
    output_limit = top.get("max_completion_tokens") if isinstance(top, dict) else None
    if isinstance(output_limit, int) and not isinstance(output_limit, bool) and output_limit > 0:
        if task["output_tokens"] > output_limit:
            return False
    return True


def _profiles(config: dict[str, Any], key: str) -> list[dict[str, Any]]:
    values = config.get(key, [])
    if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
        raise ValueError(f"{key} must be a list of objects")
    identifiers = [_text(value.get("id"), f"{key}.id") for value in values]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError(f"{key} contains duplicate ids")
    return values


def _priority(profile: dict[str, Any]) -> int:
    # The priority is an ordering key; it need not encode price or model quality.
    value = profile.get("priority", 100)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("profile.priority must be an integer")
    return value


def _minimum_supported(task: dict[str, Any], candidate: dict[str, Any]) -> list[str]:
    allowed = candidate.get("efforts", [])
    if not isinstance(allowed, list):
        raise ValueError("candidate.efforts must be a list")
    floor_rank = EFFORTS.index(_effort_floor(task))
    return [effort for effort in EFFORTS if effort in allowed and EFFORTS.index(effort) >= floor_rank]


def build_candidates(
    task: dict[str, Any], config: dict[str, Any], runtime: dict[str, Any], free_catalog: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return only candidates that satisfy local hard constraints.

    ``allow_legacy_auto=False`` keeps a legacy model only when a usable modern
    model of its family is absent. Legacy Terra additionally requires no usable
    Sol model. An exact compatibility preference or model pin can bypass these
    legacy preferences, never the hard capability/availability/privacy gates.
    ``True`` permits old and modern profiles to coexist.
    """
    task = normalize_task(task)
    if not isinstance(config, dict) or not isinstance(runtime, dict):
        raise ValueError("config and runtime must be objects")
    policy = config.get("policy", {})
    if not isinstance(policy, dict):
        raise ValueError("config.policy must be an object")
    max_effort = _enum(policy.get("max_effort", "xhigh"), EFFORTS, "max_effort")
    allow_legacy = _boolean(policy.get("allow_legacy_auto", False), "allow_legacy_auto")
    models = runtime.get("models", [])
    if not isinstance(models, list) or any(not isinstance(model, dict) for model in models):
        raise ValueError("runtime.models must be a list of objects")
    inventory: dict[str, dict[str, Any]] = {}
    for model in models:
        identifier = _text(model.get("id"), "runtime model id")
        if identifier in inventory:
            raise ValueError("runtime.models contains duplicate ids")
        inventory[identifier] = model

    raw_gpt: list[tuple[dict[str, Any], dict[str, Any]]] = []
    floor_rank = EFFORTS.index(_effort_floor(task))
    for profile in _profiles(config, "gpt_profiles"):
        identifier = profile["id"]
        family = _enum(profile.get("family"), FAMILIES, "profile.family")
        description = _text(profile.get("description"), "profile.description")
        priority = _priority(profile)
        _boolean(profile.get("legacy", False), "profile.legacy")
        if identifier not in inventory or not _family_allowed(task, family):
            continue
        available_efforts = inventory[identifier].get("efforts", [])
        if not isinstance(available_efforts, list):
            raise ValueError("runtime model efforts must be a list")
        allowed = [effort for effort in EFFORTS if effort in available_efforts and EFFORTS.index(effort) <= EFFORTS.index(max_effort)]
        sufficient = [effort for effort in allowed if EFFORTS.index(effort) >= floor_rank]
        if not sufficient:
            continue
        default_effort = inventory[identifier].get("default_effort")
        if default_effort not in sufficient:
            default_effort = sufficient[0]
        raw_gpt.append((profile, {
            "id": identifier,
            "model_id": identifier,
            "backend": "codex",
            "family": family,
            "description": description,
            "efforts": allowed,
            "default_effort": default_effort,
            "priority": priority,
        }))

    modern_families = {candidate["family"] for profile, candidate in raw_gpt if not profile.get("legacy", False)}
    sol_available = any(candidate["family"] == "sol" for _, candidate in raw_gpt)
    candidates: list[dict[str, Any]] = []
    for profile, candidate in raw_gpt:
        explicit_legacy = candidate["id"] in (task["requested_model"], task["compatibility_model"])
        if profile.get("legacy", False) and not allow_legacy and not explicit_legacy:
            if candidate["family"] in modern_families:
                continue
            if candidate["family"] == "terra" and sol_available:
                continue
        candidates.append(candidate)

    free_profiles = _profiles(config, "free_profiles")
    if free_profiles and _free_task_allowed(task):
        # The backend is the single source for catalog price/modality validation.
        from openrouter_backend import get_free_models

        free_records = get_free_models(free_catalog, modality="text")
        by_id = {record["id"]: record for record in free_records}
        for profile in free_profiles:
            if not _boolean(profile.get("enabled", False), "free profile.enabled"):
                continue
            identifier = profile["id"]
            kinds = profile.get("kinds", [])
            if not isinstance(kinds, list) or any(kind not in TASK_KINDS for kind in kinds):
                raise ValueError("free profile.kinds must contain supported task kinds")
            max_complexity = _integer(profile.get("max_complexity", 1), "free profile.max_complexity", 1, 4)
            if task["kind"] not in kinds or task["complexity"] > max_complexity or identifier not in by_id:
                continue
            record = by_id[identifier]
            if not _free_context_fits(task, record):
                continue
            description = _text(profile.get("description"), "free profile.description")
            candidates.append({
                "id": identifier,
                "model_id": identifier,
                "backend": "openrouter",
                "family": "free",
                "description": description + " The low effort value is a local routing label; this text worker receives no native reasoning-effort parameter and executes no local tools.",
                "efforts": ["low"],
                "default_effort": "low",
                "priority": _priority(profile),
                "kinds": list(kinds),
                "max_complexity": max_complexity,
                "model_record": copy.deepcopy(record),
            })

    # The full free catalog is exposed through free_agents.py, whereas the
    # Decisions API receives a bounded shortlist of text workers. Exact pins
    # can still target ANY currently verified free text model, even beyond it.
    if _free_task_allowed(task) and policy.get("dynamic_free_profiles", False) is True:
        from openrouter_backend import get_free_models

        limit = _integer(policy.get("max_dynamic_free_candidates", 16), "max_dynamic_free_candidates", 1, 20)
        records = get_free_models(free_catalog, modality="text")
        already = {candidate["id"] for candidate in candidates}
        pinned = task.get("requested_model")
        disabled = {p["id"] for p in free_profiles if p.get("enabled") is False}
        pool = [record for record in records if record["id"] not in already
                and _free_context_fits(task, record)
                and (record["id"] not in disabled or record["id"] == pinned)]
        # Deterministic neutral order; do NOT claim it's a quality ranking.
        pool.sort(key=lambda row: row["id"])
        if pinned:
            pool = [record for record in pool if record["id"] == pinned]
        else:
            remaining = max(0, limit - sum(c["family"] == "free" for c in candidates))
            pool = pool[:remaining]
        for record in pool:
            identifier = record["id"]
            candidates.append({
                "id": identifier,
                "model_id": identifier,
                "backend": "openrouter",
                "family": "free",
                "description": "Catalog-discovered free text worker; returns text only and has no local tools or native Codex reasoning effort. No model-quality or latency benchmark is implied.",
                "efforts": ["low"], "default_effort": "low",
                "priority": 200,
                "kinds": list(sorted(TASK_KINDS - {"visual_qa"})),
                "max_complexity": 2,
                "model_record": copy.deepcopy(record),
            })

    if task["requested_model"] is not None:
        candidates = [candidate for candidate in candidates if candidate["id"] == task["requested_model"]]
    if len({candidate["id"] for candidate in candidates}) != len(candidates):
        raise ValueError("candidate ids must be unique across backends")
    return sorted(candidates, key=lambda candidate: (candidate["priority"], candidate["id"]))


def enforce_selection(
    task: dict[str, Any], candidates: list[dict[str, Any]], selected_id: str, effort: str,
    effort_floor: bool = True,
) -> dict[str, Any]:
    """Validate the selected ID and clamp effort to the eligible model's range.

    Below-floor choices are raised to the first supported sufficient level. A
    request above the supported/capped range is bounded at its maximum; candidate
    construction has already required that this maximum meets the task floor.
    ``effort_floor=False`` keeps the decider's effort exactly when the model
    supports it, otherwise the nearest supported level (no complexity floor).
    """
    task = normalize_task(task)
    if task["requested_model"] is not None and selected_id != task["requested_model"]:
        raise ValueError("selected model violates the exact requested_model pin")
    matches = [candidate for candidate in candidates if candidate.get("id") == selected_id]
    if len(matches) != 1:
        raise ValueError("selected model is not one unique eligible candidate")
    candidate = matches[0]
    family = candidate.get("family")
    if not _family_allowed(task, family):
        raise ValueError("selected model violates the task family floor")
    if candidate.get("backend") == "openrouter":
        if not _free_task_allowed(task) or family != "free":
            raise ValueError("free worker violates task constraints")
        if task["kind"] not in candidate.get("kinds", []):
            raise ValueError("free worker is not enabled for this task kind")
        max_complexity = _integer(candidate.get("max_complexity"), "candidate.max_complexity", 1, 4)
        if task["complexity"] > max_complexity or not _free_context_fits(task, candidate.get("model_record")):
            raise ValueError("free worker exceeds its task complexity or context limit")
        return {"selected_id": selected_id, "effort": "low", "reason": "free-worker-policy-label-no-native-effort"}
    if candidate.get("backend") != "codex":
        raise ValueError("selected candidate has an unsupported backend")
    desired = _enum(effort, EFFORTS, "selected effort")
    if not effort_floor:
        supported = [level for level in EFFORTS if level in candidate.get("efforts", [])]
        if not supported:
            raise ValueError("selected candidate has no supported effort")
        adjusted = next((level for level in supported if EFFORTS.index(level) >= EFFORTS.index(desired)), supported[-1])
        reason = "accepted" if adjusted == desired else "effort-adjusted-to-supported-range"
        return {"selected_id": selected_id, "effort": adjusted, "reason": reason}
    sufficient = _minimum_supported(task, candidate)
    if not sufficient:
        raise ValueError("selected candidate has no effort at or above the task floor")
    desired_rank = max(EFFORTS.index(desired), EFFORTS.index(_effort_floor(task)))
    adjusted = next((level for level in sufficient if EFFORTS.index(level) >= desired_rank), sufficient[-1])
    reason = "accepted" if adjusted == desired else "effort-adjusted-to-supported-range-and-task-floor"
    return {"selected_id": selected_id, "effort": adjusted, "reason": reason}


def fallback_route(task: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose a deterministic, sufficient fallback without changing an exact pin."""
    task = normalize_task(task)
    if not candidates:
        raise ValueError("no eligible model; no automatic model substitution is allowed")
    # Revalidate the task gates if a caller supplies an old or hand-built list.
    eligible = []
    for candidate in candidates:
        try:
            choice = enforce_selection(task, [candidate], candidate.get("id"), _effort_floor(task))
        except ValueError:
            continue
        eligible.append((candidate, choice))
    if not eligible:
        raise ValueError("no eligible fallback satisfies the current task constraints")
    eligible.sort(key=lambda item: (item[0].get("priority", 100), item[0]["id"]))

    preferred = None
    if task["requested_model"] is not None:
        preferred = next((item for item in eligible if item[0]["id"] == task["requested_model"]), None)
        reason = "exact-model-pin"
    elif task["compatibility_model"] is not None:
        preferred = next((item for item in eligible if item[0]["id"] == task["compatibility_model"]), None)
        reason = "compatibility-preference"
    if preferred is None and _free_task_allowed(task):
        preferred = next((item for item in eligible if item[0]["backend"] == "openrouter"), None)
        reason = "eligible-free-worker"
    if preferred is None:
        if task["risk"] == "high" or task["complexity"] == 4:
            order = ("astra",)
        elif task["kind"] in SOL_KINDS or task["complexity"] >= 3:
            order = ("sol", "astra", "terra", "luna")
        else:
            order = ("luna", "terra", "sol", "astra")
        for family in order:
            preferred = next((item for item in eligible if item[0]["family"] == family), None)
            if preferred is not None:
                break
        reason = "deterministic-family-fallback"
    if preferred is None:
        raise ValueError("no fallback satisfies the required model family")
    return {**preferred[1], "reason": reason}


def routing_state(task: dict[str, Any]) -> dict[str, Any] | None:
    """Build external decision state from an explicit metadata allowlist.

    ``metadata_only`` requires an already-sanitized summary with an explicit
    approval flag. ``no_external`` and an absent approval produce ``None`` so the
    host uses local policy. No raw source fields, filenames, or credentials are
    copied from arbitrary task keys. This is not a content redaction detector.
    """
    task = normalize_task(task)
    if task["data_policy"] == "no_external":
        return None
    if task["data_policy"] == "metadata_only":
        if not task["summary_approved"] or not task["summary"].strip():
            return None
        summary = task["summary"]
    else:
        summary = task["objective"]
    fields = (
        "kind", "complexity", "risk", "needs_tools", "input_tokens", "output_tokens",
        "data_policy", "implementation_failures", "failure_kind", "requested_model", "compatibility_model",
    )
    result = {"summary": summary, **{field: task[field] for field in fields}}
    try:
        size = len(json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError("routing state must be finite UTF-8 JSON data") from exc
    if size > MAX_ROUTING_STATE_BYTES:
        raise ValueError(f"routing state exceeds {MAX_ROUTING_STATE_BYTES} UTF-8 bytes")
    return result


def automatic_metadata_state(task: dict[str, Any]) -> dict[str, Any] | None:
    """Classified task *metadata* only, never user prompt, summary or paths.

    Used only when Auto Routing was explicitly enabled in local config. The
    existing routing_state() approval path is unchanged for user-authored text.
    "summary_approved" is never modified or implicitly set to True.
    """
    task = normalize_task(task)
    if task["data_policy"] == "no_external":
        return None
    fields = ("kind", "complexity", "risk", "needs_tools", "input_tokens",
              "output_tokens", "data_policy", "implementation_failures", "failure_kind")
    return {"summary": "Task classification metadata only; no user task text is included.",
            **{field: task[field] for field in fields}}
