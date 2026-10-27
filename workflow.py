"""Durable native-host orchestration; never starts tools, agents, or networks.

All claims use SQLite write transactions. A claimed dispatch with an uncertain
host outcome is deliberately never replayed. Agent IDs, plans and verifier
results are host-reported evidence, not cryptographic provider attestation.
Worker turn completion and independent project acceptance are separate states.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path, PureWindowsPath
import re
import sqlite3
import uuid

from decision import is_decision_model
from policy import EFFORTS, normalize_task


DEFAULT_CONFIG = {
    "coordinator_model": "gpt-6-luna", "coordinator_effort": "medium",
    "integrator_model": "gpt-6.1-sol", "integrator_effort": "high",
    "max_parallel": 2,
}
MAX_PROGRESS_EVENTS = 200
MAX_EVENT_TEXT = 2048
NATIVE_DECISION_SOURCE = "native_coordinator_host_reported"
NATIVE_WORKER_MODELS = ("gpt-6.1-sol", "gpt-6.1-terra")
NATIVE_WORKER_EFFORTS = ("low", "medium", "high", "xhigh", "max")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9./:_-]{0,127}\Z")
_HASH = re.compile(r"[0-9a-fA-F]{64}\Z")
_VERIFICATION_UNAVAILABLE_REASONS = frozenset({
    "verifier_timeout_no_retry", "verifier_cannot_launch", "verifier_execution_invalid",
    "verifier_contract_invalid", "verifier_environment_error",
})


class WorkflowConflict(ValueError):
    """A transition or claim is unavailable in the current durable state."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(value, label, maximum=MAX_EVENT_TEXT):
    if not isinstance(value, str) or not value.strip() or "\x00" in value or len(value) > maximum:
        raise ValueError(f"{label} must be nonempty text of at most {maximum} characters")
    return value


def _redact(value):
    """Best-effort obvious credential filtering, not a secret-discovery claim."""
    if not isinstance(value, str):
        raise ValueError("event text must be a string")
    value = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|\Z)",
                   "[REDACTED PRIVATE KEY]", value, flags=re.S)
    value = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer [REDACTED]", value)
    value = re.sub(r"(?i)((?<![\w])[\"']?(?:[A-Za-z][A-Za-z0-9_]*_)?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|secret|authorization)[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
                   r"\1[REDACTED]", value)
    value = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]{8,})\b",
                   "[REDACTED]", value)
    return value[:MAX_EVENT_TEXT]


def _model(value):
    if (not isinstance(value, str) or not _MODEL.fullmatch(value) or "decisions" in value.lower()
            or _redact(value) != value):
        raise ValueError("invalid native worker model")
    return value


def _config(raw):
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or set(raw) - set(DEFAULT_CONFIG):
        raise ValueError("unknown workflow configuration fields")
    result = {**DEFAULT_CONFIG, **raw}
    for role in ("coordinator", "integrator"):
        _model(result[f"{role}_model"])
        if result[f"{role}_effort"] not in EFFORTS:
            raise ValueError("unsupported configured reasoning effort")
    count = result["max_parallel"]
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 16:
        raise ValueError("max_parallel must be an integer from 1 to 16")
    return result


def _project(value):
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("project must be an explicit absolute directory")
    path = path.resolve(strict=True)
    if not path.is_dir():
        raise ValueError("project must be an existing directory")
    return path


def _scope(project, value):
    _text(value, "write scope", 1024)
    windows = PureWindowsPath(value)
    parts = value.replace("\\", "/").split("/")
    if (windows.is_absolute() or windows.drive or value.startswith(("/", "\\"))
            or any(part in ("", ".", "..") or part[-1:] in (" ", ".")
                   or any(c in part for c in ':*?[]{}<>|') or any(ord(c) < 32 for c in part) for part in parts)):
        raise ValueError("write scopes require explicit relative paths without traversal or globbing")
    resolved = project.joinpath(*parts).resolve()
    try:
        relative = resolved.relative_to(project)
    except ValueError as exc:
        raise ValueError("write scope escapes project through a link") from exc
    if not relative.parts:
        raise ValueError("write scope must identify a path within the project")
    return "/".join(parts), relative.as_posix().casefold()


def _overlap(left, right):
    return any(a == b or a.startswith(b + "/") or b.startswith(a + "/") for a in left for b in right)


def _tasks(project, raw):
    if not isinstance(raw, list) or not 1 <= len(raw) <= 256:
        raise ValueError("tasks must contain 1-256 tasks")
    result = []
    ids = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each task must be an object")
        identifier = item.get("id")
        if not isinstance(identifier, str) or not _IDENTIFIER.fullmatch(identifier) or identifier in ids:
            raise ValueError("task ids must be unique simple identifiers")
        ids.add(identifier)
        normalized = normalize_task(item)
        if normalized["data_policy"] != "metadata_only" or normalized["summary_approved"]:
            raise ValueError("native workflow requires metadata_only without an external summary")
        scopes = item.get("write_scopes")
        if not isinstance(scopes, list) or len(scopes) > 128:
            raise ValueError("write_scopes must be an explicit list (empty for read-only work)")
        paths = [_scope(project, scope) for scope in scopes]
        if len(set(key for _, key in paths)) != len(paths):
            raise ValueError("duplicate write scopes")
        deps = item.get("depends_on", [])
        if (not isinstance(deps, list) or any(not isinstance(dep, str) for dep in deps)
                or len(set(deps)) != len(deps) or identifier in deps):
            raise ValueError("invalid task dependencies")
        result.append({"id": identifier, **normalized, "write_scopes": [path for path, _ in paths],
                       "depends_on": list(deps), "status": "queued", "plan": None,
                       "agent_id": None, "outcome": None, "evidence": "", "progress_count": 0})
    by_id = {task["id"]: task for task in result}
    visited, visiting = set(), set()

    def visit(identifier):
        if identifier not in ids:
            raise ValueError("unknown task dependency")
        if identifier in visiting:
            raise ValueError("task dependencies contain a cycle")
        if identifier in visited:
            return
        visiting.add(identifier)
        for dependency in by_id[identifier]["depends_on"]:
            visit(dependency)
        visiting.remove(identifier)
        visited.add(identifier)

    for identifier in ids:
        visit(identifier)
    return result


def _native_plan(raw, config=None):
    """Validate host-reported routing without fabricating API call evidence."""
    if (raw.get("status") != "planned" or raw.get("backend") != "codex"
            or raw.get("simulation") is not False
            or raw.get("selected_model") not in NATIVE_WORKER_MODELS
            or raw.get("requested_effort") not in NATIVE_WORKER_EFFORTS):
        raise ValueError("unsupported native coordinator worker model or reasoning effort")
    if any(isinstance(key, str) and key.startswith("decision_") and key != "decision_source"
           for key in raw):
        raise ValueError("native coordinator plans must not claim Decisions API evidence")
    coordinator = raw.get("coordinator")
    if (not isinstance(coordinator, dict)
            or set(coordinator) != {"agent_id", "model", "effort", "provenance"}
            or coordinator.get("model") != "gpt-6-luna" or coordinator.get("effort") != "medium"
            or coordinator.get("provenance") != "host_reported"):
        raise ValueError("native coordinator requires gpt-6-luna/medium host-reported provenance")
    agent_id = _text(coordinator.get("agent_id"), "native coordinator agent id", 256)
    if _redact(agent_id) != agent_id or any(character.isspace() or ord(character) < 32 for character in agent_id):
        raise ValueError("native coordinator agent id must not contain credentials or whitespace")
    if config is not None and (config["coordinator_model"], config["coordinator_effort"]) != (
            coordinator["model"], coordinator["effort"]):
        raise ValueError("native coordinator differs from configured coordinator")
    return {"status": "planned", "backend": "codex", "simulation": False,
            "decision_source": NATIVE_DECISION_SOURCE, "selected_model": raw["selected_model"],
            "requested_effort": raw["requested_effort"], "coordinator": dict(coordinator)}


def _plan(raw, config=None):
    if not isinstance(raw, dict):
        raise ValueError("plan must be an object")
    if raw.get("status") == "blocked":
        return {"status": "blocked", "reason": _redact(str(raw.get("reason", "decision_blocked")))}
    if raw.get("decision_source") == NATIVE_DECISION_SOURCE:
        return _native_plan(raw, config)
    if (raw.get("status") != "planned" or raw.get("decision_source") != "luna_decisions"
            or raw.get("simulation") is not False or raw.get("backend") != "codex"
            or any(raw.get(flag) is not True for flag in
                   ("decision_call_attempted", "decision_response_received", "decision_model_called"))
            or not is_decision_model(raw.get("decision_model"))):
        raise ValueError("a fresh valid Luna Decisions native-host plan is required")
    model = _model(raw.get("selected_model"))
    effort = raw.get("requested_effort")
    if effort not in EFFORTS:
        raise ValueError("unsupported native reasoning effort")
    metrics = raw.get("decision_metrics")
    worker = raw.get("worker", {})
    if not isinstance(worker, dict) or worker.get("model_id", model) != model:
        raise ValueError("plan worker differs from selection")
    if (not isinstance(metrics, dict) or not is_decision_model(metrics.get("model"))
            or not isinstance(metrics.get("selected_id"), str)
            or _redact(metrics["selected_id"]) != metrics["selected_id"]
            or metrics.get("selected_id") != worker.get("id", model) or metrics.get("effort") != effort):
        raise ValueError("plan lacks matching validated decision metadata")
    return {"status": "planned", "selected_model": model, "requested_effort": effort,
            "backend": "codex", "simulation": False, "decision_source": "luna_decisions",
            "decision_model": raw["decision_model"], "decision_call_attempted": True,
            "decision_response_received": True, "decision_model_called": True,
            "decision_response_id": _redact(str(raw.get("decision_response_id") or "")),
            "decision_metrics": {"model": metrics["model"], "selected_id": metrics["selected_id"], "effort": effort}}


class WorkflowStore:
    def __init__(self, state_dir):
        directory = Path(state_dir)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "workflow.sqlite3"
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, document TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, "
                               "job_id TEXT NOT NULL, kind TEXT NOT NULL, task_id TEXT, data TEXT NOT NULL, created_at TEXT NOT NULL)")
            connection.execute("CREATE INDEX IF NOT EXISTS events_job_seq ON events(job_id, seq)")

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def _load(self, connection, job_id):
        row = connection.execute("SELECT document FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("unknown workflow job")
        return json.loads(row["document"])

    def _event(self, connection, doc, kind, task_id=None, **data):
        connection.execute("INSERT INTO events(job_id,kind,task_id,data,created_at) VALUES(?,?,?,?,?)",
                           (doc["job"]["id"], kind, task_id, json.dumps(data), _now()))

    def _change(self, job_id, operation):
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                doc = self._load(connection, job_id)
                result = operation(connection, doc)
                connection.execute("UPDATE jobs SET document=? WHERE id=?", (json.dumps(doc), job_id))
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return result if result is not None else self.snapshot(job_id)

    @staticmethod
    def _task(doc, task_id):
        for task in doc["tasks"]:
            if task["id"] == task_id:
                return task
        raise ValueError("unknown workflow task")

    @staticmethod
    def _require(value, allowed):
        if value not in allowed:
            raise WorkflowConflict("transition unavailable from " + str(value))

    def _task_open(self, doc, task_id, allowed):
        self._require(doc["job"]["status"], {"executing"})
        self._require(doc["job"]["integration"]["status"], {"pending"})
        task = self._task(doc, task_id)
        self._require(task["status"], allowed)
        return task

    def create(self, project, tasks, config=None):
        project = _project(project)
        doc = {"job": {"id": uuid.uuid4().hex, "project": str(project), "config": _config(config),
                       "status": "executing", "created_at": _now(),
                       "integration": {"status": "pending", "agent_id": None},
                       "verification": None, "verification_attempt": None},
               "tasks": _tasks(project, tasks)}
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute("INSERT INTO jobs VALUES(?,?)", (doc["job"]["id"], json.dumps(doc)))
                self._event(connection, doc, "job_created", task_count=len(doc["tasks"]),
                            evidence_scope="host_reported_local_ledger")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self.snapshot(doc["job"]["id"])

    def snapshot(self, job_id, after=0, limit=200):
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise ValueError("after must be a nonnegative event cursor")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("event limit must be an integer from 1 to 500")
        with self._connection() as connection:
            connection.execute("BEGIN")
            doc = self._load(connection, job_id)
            rows = connection.execute("SELECT * FROM events WHERE job_id=? AND seq>? ORDER BY seq LIMIT ?",
                                      (job_id, after, limit + 1)).fetchall()
            connection.commit()
        doc["events"] = [{"seq": row["seq"], "kind": row["kind"], "task_id": row["task_id"],
                          "data": json.loads(row["data"]), "created_at": row["created_at"]} for row in rows[:limit]]
        doc["cursor"] = doc["events"][-1]["seq"] if doc["events"] else after
        doc["has_more"] = len(rows) > limit
        return doc

    def begin_decision(self, job_id, task_id):
        def change(connection, doc):
            task = self._task_open(doc, task_id, {"queued", "routing_blocked"})
            task["status"] = "deciding"
            task["plan"] = None
            self._event(connection, doc, "decision_started", task_id)
        return self._change(job_id, change)

    def record_decision(self, job_id, task_id, plan):
        def change(connection, doc):
            task = self._task_open(doc, task_id, {"deciding"})
            saved_plan = _plan(plan, doc["job"]["config"])
            task["plan"] = saved_plan
            task["status"] = "ready" if saved_plan["status"] == "planned" else "routing_blocked"
            self._event(connection, doc, "decision_recorded", task_id, status=task["status"], plan=saved_plan)
        return self._change(job_id, change)

    def claim_dispatch(self, job_id, task_id):
        def change(connection, doc):
            task = self._task_open(doc, task_id, {"ready"})
            active = [item for item in doc["tasks"] if item["status"] in {"dispatching", "running"}]
            if len(active) >= doc["job"]["config"]["max_parallel"]:
                raise WorkflowConflict("max_parallel reached")
            if any(self._task(doc, dep)["status"] != "completed" for dep in task["depends_on"]):
                raise WorkflowConflict("dependencies are not completed")
            project = _project(doc["job"]["project"])
            scopes = [_scope(project, scope)[1] for scope in task["write_scopes"]]
            if any(_overlap(scopes, [_scope(project, scope)[1] for scope in other["write_scopes"]]) for other in active):
                raise WorkflowConflict("write scopes overlap an active task")
            task["status"] = "dispatching"
            task["dispatch_id"] = uuid.uuid4().hex
            plan = task["plan"]
            provenance = {"provenance": plan["decision_source"]}
            if plan["decision_source"] == NATIVE_DECISION_SOURCE:
                provenance["coordinator"] = dict(plan["coordinator"])
            self._event(connection, doc, "dispatch_claimed", task_id, dispatch_id=task["dispatch_id"],
                        model=plan["selected_model"], effort=plan["requested_effort"], **provenance)
            return {"job_id": job_id, "task_id": task_id, "dispatch_id": task["dispatch_id"],
                    "project": str(project), "model": plan["selected_model"], "effort": plan["requested_effort"],
                    "objective": task["objective"], "write_scopes": task["write_scopes"],
                    "depends_on": task["depends_on"], **provenance,
                    "agent_id_source": "host_reported", "worker_header": "[LUNA ROUTER WORKER]"}
        return self._change(job_id, change)

    def started(self, job_id, task_id, agent_id):
        agent_id = _text(agent_id, "native agent id", 256)
        if _redact(agent_id) != agent_id:
            raise ValueError("native agent id must not contain credentials")
        def change(connection, doc):
            task = self._task_open(doc, task_id, {"dispatching"})
            if any(other["agent_id"] == agent_id for other in doc["tasks"]):
                raise WorkflowConflict("native agent id already recorded")
            task.update(status="running", agent_id=agent_id)
            self._event(connection, doc, "agent_started", task_id, agent_id=agent_id, evidence_scope="host_reported")
        return self._change(job_id, change)

    def progress(self, job_id, task_id, summary):
        summary = _redact(_text(summary, "progress summary", 16384))
        def change(connection, doc):
            task = self._task_open(doc, task_id, {"running"})
            if task["progress_count"] >= MAX_PROGRESS_EVENTS:
                raise WorkflowConflict("progress event limit reached")
            task["progress_count"] += 1
            self._event(connection, doc, "agent_progress", task_id, summary=summary)
        return self._change(job_id, change)

    @staticmethod
    def _outcome(value):
        if not isinstance(value, str) or value not in {"passed", "completed", "failed", "blocked", "unknown"}:
            raise ValueError("unsupported host turn outcome")
        return "completed" if value == "passed" else value

    def complete(self, job_id, task_id, outcome, evidence=""):
        outcome, evidence = self._outcome(outcome), _redact(evidence)
        def change(connection, doc):
            task = self._task_open(doc, task_id, {"running"})
            task.update(status=outcome, outcome=outcome, evidence=evidence)
            self._event(connection, doc, "agent_turn_completed", task_id, outcome=outcome,
                        evidence=evidence, acceptance="not_verified")
        return self._change(job_id, change)

    def claim_integration(self, job_id):
        def change(connection, doc):
            job = doc["job"]
            self._require(job["status"], {"executing"})
            self._require(job["integration"]["status"], {"pending"})
            if any(task["status"] != "completed" for task in doc["tasks"]):
                raise WorkflowConflict("all worker turns must complete before integration")
            config = job["config"]
            job["integration"] = {"status": "dispatching", "agent_id": None,
                                  "model": config["integrator_model"], "effort": config["integrator_effort"],
                                  "provenance": "user_configured_integrator"}
            job["status"] = "integrating"
            self._event(connection, doc, "integration_claimed", **job["integration"])
            return {"job_id": job_id, "project": str(_project(job["project"])),
                    "model": config["integrator_model"], "effort": config["integrator_effort"],
                    "provenance": "user_configured_integrator", "tasks": doc["tasks"]}
        return self._change(job_id, change)

    def integration_started(self, job_id, agent_id):
        agent_id = _text(agent_id, "native agent id", 256)
        if _redact(agent_id) != agent_id:
            raise ValueError("native agent id must not contain credentials")
        def change(connection, doc):
            job = doc["job"]
            self._require(job["status"], {"integrating"})
            self._require(job["integration"]["status"], {"dispatching"})
            if any(task["agent_id"] == agent_id for task in doc["tasks"]):
                raise WorkflowConflict("integrator must have a separate native agent id")
            job["integration"].update(status="running", agent_id=agent_id)
            self._event(connection, doc, "integration_started", agent_id=agent_id, evidence_scope="host_reported")
        return self._change(job_id, change)

    def integration_complete(self, job_id, outcome, evidence=""):
        outcome, evidence = self._outcome(outcome), _redact(evidence)
        def change(connection, doc):
            job = doc["job"]
            self._require(job["status"], {"integrating"})
            self._require(job["integration"]["status"], {"running"})
            job["integration"].update(status=outcome, outcome=outcome, evidence=evidence)
            job["status"] = "awaiting_verification" if outcome == "completed" else "integration_" + outcome
            self._event(connection, doc, "integration_turn_completed", outcome=outcome,
                        evidence=evidence, acceptance="not_verified")
        return self._change(job_id, change)

    def claim_verification(self, job_id):
        """Claim before reading/running a check; never replay an uncertain run."""
        def change(connection, doc):
            job = doc["job"]
            self._require(job["status"], {"awaiting_verification", "verification_failed"})
            self._require(job["integration"]["status"], {"completed"})
            attempt = {"status": "claimed", "claim_id": uuid.uuid4().hex, "claimed_at": _now()}
            job.update(status="verifying", verification_attempt=attempt)
            self._event(connection, doc, "verification_claimed", **attempt,
                        evidence_scope="host_reported_verifier")
        return self._change(job_id, change)

    def verification_unavailable(self, job_id, result):
        """Persist infrastructure failure; an explicit new claim may retry it.

        A check_id is unavailable only if the contract could not be read or the
        host failed before obtaining it. Never invent a successful check digest.
        Unknown fields, exception text, command output and paths are not stored.
        """
        if (not isinstance(result, dict) or result.get("evidence_kind") != "worker"
                or not isinstance(result.get("outcome"), str)
                or result["outcome"] not in {"blocked", "unknown"}
                or not isinstance(result.get("reason"), str)
                or result["reason"] not in _VERIFICATION_UNAVAILABLE_REASONS):
            raise ValueError("known verifier infrastructure failure required")
        check_id = result.get("check_id")
        if check_id is None:
            if result["reason"] not in {"verifier_contract_invalid", "verifier_environment_error"}:
                raise ValueError("verifier infrastructure result requires a check hash")
        elif not isinstance(check_id, str) or not _HASH.fullmatch(check_id):
            raise ValueError("verifier check_id must be a SHA256 hash when available")
        record = {key: result[key] for key in ("outcome", "evidence_kind", "reason")}
        record["check_id"] = check_id
        if "exit_code" in result:
            if isinstance(result["exit_code"], bool) or not isinstance(result["exit_code"], int):
                raise ValueError("unavailable verifier exit_code must be an integer")
            record["exit_code"] = result["exit_code"]
        def change(connection, doc):
            job = doc["job"]
            self._require(job["status"], {"verifying"})
            self._require(job["integration"]["status"], {"completed"})
            attempt = {**record, "status": "unavailable", "recorded_at": _now(),
                       "claim_id": job["verification_attempt"]["claim_id"]}
            job.update(status="awaiting_verification", verification_attempt=attempt)
            self._event(connection, doc, "verification_unavailable", **attempt,
                        acceptance="not_verified", evidence_scope="host_reported_verifier")
        return self._change(job_id, change)

    def verify(self, job_id, result):
        if not isinstance(result, dict) or result.get("evidence_kind") != "verifier":
            raise ValueError("independent verifier evidence required")
        for key in ("check_id", "evidence_sha256"):
            if not isinstance(result.get(key), str) or not _HASH.fullmatch(result[key]):
                raise ValueError("verifier check_id and evidence_sha256 must be SHA256 hashes")
        code = result.get("exit_code")
        outcome = result.get("outcome")
        if (isinstance(code, bool) or not isinstance(code, int) or code < 0 or code in (126, 127)
                or not isinstance(outcome, str) or outcome not in {"passed", "failed"}
                or (outcome == "passed") != (code == 0)):
            raise ValueError("verifier outcome must agree with a valid process exit code")
        record = {key: result[key] for key in ("outcome", "evidence_kind", "exit_code", "check_id", "evidence_sha256")}
        def change(connection, doc):
            job = doc["job"]
            self._require(job["status"], {"verifying", "awaiting_verification", "verification_failed"})
            self._require(job["integration"]["status"], {"completed"})
            claimed = job["status"] == "verifying"
            job["verification_attempt"] = {**record, "status": outcome, "recorded_at": _now(),
                                           "claim_id": job["verification_attempt"]["claim_id"] if claimed else None}
            job["verification"] = record
            job["status"] = "completed" if outcome == "passed" else "verification_failed"
            self._event(connection, doc, "verification_recorded", **record, evidence_scope="host_reported_verifier")
        return self._change(job_id, change)
