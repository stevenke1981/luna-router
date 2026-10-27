"""Independent local expense checks for a *future direct OpenAI router* only.

The current router dispatches through OpenRouter and ChatGPT-authenticated
Codex, neither of which uses this monetary policy. OpenRouter account / key /
guardrail spend caps are external. This module intentionally cannot make any
HTTP request and never reads credentials.

A future direct OpenAI API adapter MUST invoke ``guard_for_backend`` before its
request and settle the result after completion. Failing to do so would bypass
this module; this package does not claim to implement a direct OpenAI router.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ledger import Ledger

OPENAI_DIRECT = "openai_router"
SUPPORTED = frozenset({"openrouter", "codex", OPENAI_DIRECT})


class FeeConfigurationError(ValueError):
    """An enabled direct OpenAI route has no valid fee policy."""


def _openai_policy(config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise FeeConfigurationError("Router config must be an object")
    fee = config.get("fee_control")
    if not isinstance(fee, dict) or not isinstance(fee.get(OPENAI_DIRECT), dict):
        raise FeeConfigurationError("Missing fee_control.openai_router policy")
    direct = fee[OPENAI_DIRECT]
    if direct.get("enabled") is not True:
        raise FeeConfigurationError("Direct OpenAI API routing requires an enabled local fee check")
    for k in ("max_request_cost_usd", "max_daily_cost_usd"):
        if k not in direct:
            raise FeeConfigurationError("Missing OpenAI fee setting: " + k)
    return direct


def guard_for_backend(
    backend: str, config: dict[str, Any], *,
    state_dir: str | Path = ".state",
) -> "OpenAIExpenseGuard | None":
    """Return None for OpenRouter or Codex; enforce fees only for OpenAI direct.

    No database and no settings are inspected on the OpenRouter path.
    Unknown backend names are errors, not a loophole into an exempt path.
    """
    if backend not in SUPPORTED:
        raise FeeConfigurationError("Unsupported cost-control backend")
    if backend != OPENAI_DIRECT:
        return None
    direct = _openai_policy(config)
    request_policy = config.get("policy", {})
    if not isinstance(request_policy, dict):
        raise FeeConfigurationError("policy must be an object")
    return OpenAIExpenseGuard(Path(state_dir) / "openai_costs.sqlite", request_policy, direct)


class OpenAIExpenseGuard:
    """Atomic reserved USD budget, separate from the OpenRouter request DB."""

    def __init__(self, path: Path, request_policy: dict, limits: dict):
        self.limits = dict(limits)
        self.ledger = Ledger(path, request_policy)

    def reserve(self, job: str) -> int:
        """Call *before* submitting a direct OpenAI request."""
        return self.ledger.reserve_decision(job, self.limits)

    def settle(self, reservation_id: int, actual_cost_usd: Any = None) -> dict:
        """Call after dispatch; unknown costs conservatively retain reservation."""
        return self.ledger.settle_decision(reservation_id, actual_cost_usd)

    def summary(self) -> dict:
        return self.ledger.paid_daily_summary()


def describe_fees(config: dict[str, Any]) -> dict[str, Any]:
    """Public status. No credential, provider balance or local account spend probe."""
    direct = config.get("fee_control", {}).get(OPENAI_DIRECT, {}) if isinstance(config.get("fee_control"), dict) else {}
    return {
        "decision_model": config.get("decision", {}).get("model"),
        "openrouter": {
            "local_fee_check": False,
            "spend_control": "OpenRouter account/API-key/guardrail limits",
            "account_limit_verified": False,
            "per_request_usd_limit": None,
            "daily_usd_limit": None,
        },
        "codex": {
            "local_fee_check": False,
            "spend_control": "ChatGPT subscription / native Codex usage limits",
        },
        "openai_router": {
            "implemented": False,
            "local_fee_check_required_if_enabled": True,
            "enabled": direct.get("enabled") is True if isinstance(direct, dict) else False,
            "max_request_cost_usd": direct.get("max_request_cost_usd") if isinstance(direct, dict) else None,
            "max_daily_cost_usd": direct.get("max_daily_cost_usd") if isinstance(direct, dict) else None,
        },
    }
