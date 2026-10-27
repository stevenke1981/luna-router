"""Persistent, evidence-driven two-failure escalation for one named job.

Only verified task failures advance effort or worker tier. Transport errors,
unknown completion, quotas, and tools/permissions must never be counted as
implementation failures. Never silently override an explicit model pin.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid
from typing import Any

from policy import EFFORTS, enforce_selection, normalize_task

FAMILY_TIERS = ("free", "luna", "terra", "sol", "astra")
JOB_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
ATTEMPT_OUTCOMES = frozenset({"passed", "failed", "unverified", "blocked", "unknown"})


def validate_job_id(value: str) -> str:
    if not isinstance(value, str) or not JOB_NAME.fullmatch(value):
        raise ValueError("job-id must contain 1-64 ASCII letters, digits, dot, underscore or hyphen")
    return value


def task_fingerprint(task: dict, project: Path) -> str:
    task = normalize_task(task)
    normalized = {"task": task, "project": str(Path(project).resolve())}
    return hashlib.sha256(json.dumps(normalized, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def _model_in_list(candidates: list[dict], selected_model: str) -> dict:
    matches = [candidate for candidate in candidates if candidate.get("model_id") == selected_model]
    if len(matches) != 1:
        raise ValueError("model no longer eligible or capability list stale")
    return matches[0]


def advance_route(task: dict, route: dict, candidates: list[dict]) -> tuple[dict | None, str]:
    """Advance exactly one native supported effort, then the next usable family.

    Called only after exactly two consecutive *verified* failures. A missing
    native max is NOT fabricated: xhigh is the effective ceiling if that is the
    model's highest supported level. A pinned model stops at its own ceiling.
    """
    task = normalize_task(task)
    selected = _model_in_list(candidates, route["model"])
    family = selected["family"]
    if selected["backend"] == "codex":
        allowed = [level for level in EFFORTS if level in selected.get("efforts", [])]
        if route.get("effort") not in allowed:
            raise ValueError("current native effort no longer supported")
        higher = [level for level in allowed if EFFORTS.index(level) > EFFORTS.index(route["effort"])]
        if higher:
            updated = dict(route)
            updated["effort"] = higher[0]
            return updated, f"effort_up:{route['effort']}->{higher[0]}"
    elif selected["backend"] != "openrouter":
        raise ValueError("unsupported active backend")

    if task.get("requested_model"):
        return None, "requested_model_pin_prevents_tier_change"
    old_tier = FAMILY_TIERS.index(family)
    for tier in FAMILY_TIERS[old_tier + 1:]:
        choices = sorted(
            (candidate for candidate in candidates if candidate.get("family") == tier),
            key=lambda candidate: (candidate.get("priority", 100), candidate["model_id"]),
        )
        for candidate in choices:
            try:
                new_effort = enforce_selection(
                    task, [candidate], candidate["id"], candidate["default_effort"]
                )["effort"]
            except ValueError:
                continue
            return {
                "model": candidate["model_id"], "backend": candidate["backend"],
                "effort": new_effort if candidate["backend"] == "codex" else None,
            }, f"tier_up:{family}->{tier}"
    if family == "astra":
        return None, "highest_tier_exhausted"
    return None, "no_higher_eligible_tier"


class RetryStore:
    """Atomic task-scoped state and idempotent attempt resolution (SQLite)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS retry_jobs (
                job_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                route_json TEXT NOT NULL, candidates_json TEXT NOT NULL,
                failures INTEGER NOT NULL DEFAULT 0, total_failures INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
                last_transition TEXT NOT NULL, updated_at REAL NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS retry_attempts (
                attempt_id TEXT PRIMARY KEY, job_id TEXT NOT NULL,
                route_json TEXT NOT NULL, outcome TEXT NOT NULL, evidence_kind TEXT,
                evidence_sha256 TEXT, detail_json TEXT, created_at REAL NOT NULL,
                FOREIGN KEY(job_id) REFERENCES retry_jobs(job_id)
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS retry_attempt_job ON retry_attempts(job_id)")
            db.execute("""CREATE TABLE IF NOT EXISTS retry_luna_origins (
                job_id TEXT PRIMARY KEY, decision_source TEXT NOT NULL, response_id TEXT,
                FOREIGN KEY(job_id) REFERENCES retry_jobs(job_id)
            )""")

    def connect(self):
        class Database:
            def __init__(self, path):
                self.db = sqlite3.connect(path, timeout=5)
            def __enter__(self):
                self.db.execute("PRAGMA foreign_keys = ON")
                return self.db
            def __exit__(self, exc_type, exc, tb):
                try:
                    if exc_type:
                        self.db.rollback()
                    else:
                        self.db.commit()
                finally:
                    self.db.close()
        return Database(self.path)

    @staticmethod
    def _read(db, job_id):
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM retry_jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["route"] = json.loads(result.pop("route_json"))
        result["candidates"] = json.loads(result.pop("candidates_json"))
        result["job_id"] = job_id
        origin = db.execute("SELECT decision_source,response_id FROM retry_luna_origins WHERE job_id=?", (job_id,)).fetchone()
        result["luna_origin_verified"] = origin is not None
        result["luna_origin_response_id"] = origin["response_id"] if origin else None
        return result

    def get(self, job_id: str) -> dict | None:
        job_id = validate_job_id(job_id)
        with self.connect() as db:
            return self._read(db, job_id)

    def register(self, job_id: str, fingerprint: str, plan: dict, candidates: list[dict]) -> dict:
        job_id = validate_job_id(job_id)
        if not isinstance(fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
            raise ValueError("invalid task fingerprint")
        if plan.get("status") != "planned" or plan.get("simulation"):
            raise ValueError("only real eligible plans can be registered")
        route = {"model": plan["selected_model"], "backend": plan["backend"],
                 "effort": plan["requested_effort"]}
        selected = _model_in_list(candidates, route["model"])
        if selected["backend"] != route["backend"] or (
            route["backend"] == "codex" and route["effort"] not in selected["efforts"]
        ):
            raise ValueError("invalid planned native route")
        candidate_copy = [
            {key: candidate[key] for key in ("id", "model_id", "backend", "family", "efforts", "default_effort", "priority", "kinds", "max_complexity", "model_record") if key in candidate}
            for candidate in candidates
        ]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = self._read(db, job_id)
            if existing is not None:
                if existing["fingerprint"] != fingerprint:
                    raise ValueError("job-id belongs to a different task or project")
                return existing
            db.execute("""INSERT INTO retry_jobs
                (job_id, fingerprint, route_json, candidates_json, status, last_transition, updated_at)
                VALUES (?,?,?,?,?,?,?)""", (
                    job_id, fingerprint, json.dumps(route, ensure_ascii=False),
                    json.dumps(candidate_copy, ensure_ascii=False), "ready", "initial_route", time.time(),
                ))
            if (plan.get("decision_source") in {"luna_decisions", "luna_decisions_cache"}
                    and plan.get("decision_model") == "openai/gpt-6-luna-decisions"
                    and isinstance(plan.get("decision_metrics"), dict)):
                db.execute("INSERT INTO retry_luna_origins VALUES (?,?,?)",
                           (job_id, plan["decision_source"], plan.get("decision_response_id")))
            return self._read(db, job_id)

    def begin_attempt(self, job_id: str, fingerprint: str) -> tuple[str, dict]:
        job_id = validate_job_id(job_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = self._read(db, job_id)
            if state is None or state["fingerprint"] != fingerprint:
                raise ValueError("job not initialized for this task/project")
            if state["status"] != "ready":
                raise ValueError("job not ready; inspect status or resolve unverified attempt")
            attempt_id = uuid.uuid4().hex
            db.execute("""INSERT INTO retry_attempts
                (attempt_id, job_id, route_json, outcome, created_at)
                VALUES (?,?,?,?,?)""", (
                    attempt_id, job_id, json.dumps(state["route"]), "running", time.time(),
                ))
            db.execute("""UPDATE retry_jobs SET status='running', attempts=attempts+1,
                updated_at=? WHERE job_id=?""", (time.time(), job_id))
            return attempt_id, state["route"]

    def record_outcome(self, job_id: str, attempt_id: str, outcome: str, task: dict, *,
                       fingerprint: str, evidence_kind: str, evidence_sha256: str | None = None,
                       detail: dict | None = None, from_review: bool = False) -> dict:
        """Register one outcome. Only `failed` with external evidence counts.

        A callback can resolve one unverified completed turn, but never turn
        a blocked/unknown/running worker into a certified implementation fail.
        """
        job_id = validate_job_id(job_id)
        if outcome not in ATTEMPT_OUTCOMES:
            raise ValueError("unsupported attempt outcome")
        if evidence_kind not in ("verifier", "manual", "worker"):
            raise ValueError("invalid evidence source")
        if outcome in ("failed", "passed") and evidence_kind not in ("verifier", "manual"):
            raise ValueError("verified outcome requires an independent verifier or user-supplied evidence")
        if evidence_kind == "manual" and not (isinstance(evidence_sha256, str) and re.fullmatch("[a-f0-9]{64}", evidence_sha256)):
            raise ValueError("manual result requires evidence SHA256")
        if detail is not None and not isinstance(detail, dict):
            raise ValueError("invalid evidence metadata")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = self._read(db, job_id)
            if state is None or state["fingerprint"] != fingerprint:
                raise ValueError("job-id does not match task/project")
            db.row_factory = sqlite3.Row
            attempt = db.execute("SELECT * FROM retry_attempts WHERE attempt_id=? AND job_id=?",
                                 (attempt_id, job_id)).fetchone()
            if attempt is None:
                raise ValueError("unknown attempt-id")
            allowed_old = ("unverified",) if from_review else ("running",)
            if attempt["outcome"] not in allowed_old:
                raise ValueError("attempt already finalized or not eligible for manual feedback")
            if from_review and (state["status"] != "needs_verification" or evidence_kind != "manual" or outcome not in ("failed", "passed")):
                raise ValueError("manual feedback only resolves one unverified completed attempt")
            if not from_review and state["status"] != "running":
                raise ValueError("attempt/job not running")
            current = json.loads(attempt["route_json"])
            if current != state["route"]:
                raise ValueError("attempt route does not match current job route")
            failures = state["failures"]
            total = state["total_failures"]
            new_route = current
            transition = "unchanged"
            if outcome == "failed":
                failures += 1
                total += 1
                if failures >= 2:
                    new_route, transition = advance_route(task, current, state["candidates"])
                    failures = 0
                    new_status = "ready" if new_route is not None else "exhausted"
                    if new_route is None:
                        new_route = current
                else:
                    new_status = "ready"
                    transition = "failure_1_of_2_same_route"
            elif outcome == "passed":
                failures = 0
                new_status = "completed"
                transition = "acceptance_verified"
            elif outcome == "unverified":
                new_status = "needs_verification"
                transition = "no_acceptance_evidence"
            else:
                new_status = "paused"
                transition = "environment_or_transport_blocked_no_escalation"
            db.execute("""UPDATE retry_attempts SET outcome=?, evidence_kind=?, evidence_sha256=?,
                detail_json=? WHERE attempt_id=?""", (
                    outcome, evidence_kind, evidence_sha256, json.dumps(detail or {}, ensure_ascii=False), attempt_id,
                ))
            db.execute("""UPDATE retry_jobs SET route_json=?, failures=?, total_failures=?,
                status=?, last_transition=?, updated_at=? WHERE job_id=?""", (
                    json.dumps(new_route), failures, total, new_status, transition, time.time(), job_id,
                ))
            return self._read(db, job_id)

    def latest_attempt(self, job_id: str) -> dict | None:
        job_id = validate_job_id(job_id)
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("""SELECT attempt_id,route_json,outcome,evidence_kind,evidence_sha256,detail_json,created_at
                FROM retry_attempts WHERE job_id=? ORDER BY rowid DESC LIMIT 1""", (job_id,)).fetchone()
            if row is None:
                return None
            value = dict(row)
            value["route"] = json.loads(value.pop("route_json"))
            if value["detail_json"]:
                value["detail"] = json.loads(value.pop("detail_json"))
            else:
                value.pop("detail_json")
            return value


def summarize(state: dict | None) -> dict:
    if state is None:
        return {"status": "not_found"}
    return {key: state[key] for key in (
        "job_id", "status", "route", "failures", "total_failures", "attempts", "last_transition",
    )}


def hash_evidence_file(filename: str | Path) -> str:
    """User-supplied on-disk test report. Store only its hash, never content."""
    path = Path(filename)
    if not path.is_file() or not (0 < path.stat().st_size <= 10 * 1024 * 1024):
        raise ValueError("evidence file must exist and contain 1 byte to 10 MiB")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()
