"""Codex subscription adapter. No inference is performed during discovery.

Protocol references (checked 2026-10-26):
https://learn.chatgpt.com/docs/app-server
https://learn.chatgpt.com/docs/non-interactive-mode
https://learn.chatgpt.com/docs/cli/reference
https://learn.chatgpt.com/docs/config-file/config-reference

Only explicit runtime capabilities authorize execution. This module never
reads credential files, modifies global configuration, or retries a task.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
import os
import math
from pathlib import Path
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any


MAX_RPC_LINE = 1024 * 1024
MAX_OUTPUT_TAIL = 2 * 1024 * 1024
MAX_EVENTS = 512
MAX_WORKER_MESSAGE = 128 * 1024
SAFE_SANDBOXES = frozenset({"read-only", "workspace-write"})
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}\Z")
_EFFORT = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
_EOF = object()


class CodexBackendError(RuntimeError):
    """Sanitized adapter error; messages contain no subprocess output."""


def _positive_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("timeout must be a positive finite number")
    if not math.isfinite(value) or value <= 0:
        raise ValueError("timeout must be a positive finite number")
    return float(value)


def _executable(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("invalid executable")
    if value == "codex" and os.name == "nt":
        return _windows_native_codex() or value
    return value


def _windows_native_codex() -> str | None:
    """Find a native codex.exe; shell=False cannot launch npm's codex.cmd shim."""
    explicit = os.environ.get("CODEX_CLI_PATH", "")
    if explicit and Path(explicit).is_file():
        return explicit
    found = shutil.which("codex")
    if found is None or Path(found).suffix.lower() == ".exe":
        return found
    # npm shim: <prefix>\codex.cmd -> bundled platform binary under node_modules.
    package = Path(found).parent / "node_modules" / "@openai" / "codex"
    for root in (package / "node_modules" / "@openai", package.parent):
        for binary in sorted(root.glob("codex-win32-*/vendor/*/bin/codex.exe")):
            return str(binary)
    return None


def _stop_process(proc: Any) -> None:
    """Reap our process without waiting indefinitely or retrying its task."""
    try:
        if proc.poll() is None:
            proc.terminate()
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass
    except OSError:
        pass


def _auth_mode(executable: str, timeout: float) -> str:
    """Parse known login-status wording; never return the original output."""
    try:
        with tempfile.TemporaryFile(mode="w+b") as status_output:
            result = subprocess.run(
                [executable, "login", "status"],
                stdin=subprocess.DEVNULL,
                stdout=status_output,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
                shell=False,
            )
            if result.returncode != 0:
                return "unknown"
            status_output.seek(0)
            status = status_output.read(65536).decode("utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    prefix = r"(?im)^\s*(?:logged\s+in\s+using|authenticated\s+(?:with|using))\s+"
    chatgpt = bool(re.search(prefix + r"chatgpt\b", status))
    api_key = bool(re.search(prefix + r"(?:an?\s+)?api[ -]key\b", status))
    if chatgpt == api_key:
        return "unknown"
    return "chatgpt" if chatgpt else "api_key"


def _read_stdout(stream: Any, inbox: queue.Queue, stop: threading.Event) -> None:
    def emit(value: Any) -> None:
        while not stop.is_set():
            try:
                inbox.put(value, timeout=0.1)
                return
            except queue.Full:
                continue

    try:
        while not stop.is_set():
            line = stream.readline(MAX_RPC_LINE + 1)
            if not line:
                break
            if len(line) > MAX_RPC_LINE:
                emit(CodexBackendError("app_server_message_too_large"))
                return
            try:
                message = json.loads(line)
            except (ValueError, UnicodeError):
                emit(CodexBackendError("invalid_app_server_json"))
                return
            if not isinstance(message, dict):
                emit(CodexBackendError("invalid_app_server_message"))
                return
            emit(message)
    except (OSError, ValueError):
        if not stop.is_set():
            emit(CodexBackendError("app_server_read_failed"))
    finally:
        emit(_EOF)


def _normalize_model(entry: Any) -> dict | None:
    if not isinstance(entry, dict) or entry.get("hidden") is True:
        return None
    # `id` can be a picker identifier; `model` is the wire model value.
    model = entry.get("model") or entry.get("id")
    if not isinstance(model, str) or not _IDENTIFIER.fullmatch(model):
        return None
    efforts: list[str] = []
    raw_efforts = entry.get("supportedReasoningEfforts", [])
    if isinstance(raw_efforts, list):
        for item in raw_efforts:
            value = item.get("reasoningEffort") if isinstance(item, dict) else None
            if isinstance(value, str) and _EFFORT.fullmatch(value) and value not in efforts:
                efforts.append(value)
    default_effort = entry.get("defaultReasoningEffort")
    if default_effort not in efforts:
        default_effort = None
    # Documented compatibility behavior for older catalogs with no modalities.
    modalities = entry.get("inputModalities", ["text", "image"])
    if not isinstance(modalities, list):
        modalities = []
    modalities = list(dict.fromkeys(x for x in modalities if isinstance(x, str)))
    description = entry.get("description") or entry.get("displayName") or model
    return {
        "id": model,
        "efforts": efforts,
        "default_effort": default_effort,
        "input_modalities": modalities,
        "description": str(description)[:2000],
    }


def discover(executable: str = "codex", timeout: float = 20) -> dict:
    """Query login status and all visible model/list pages without a model turn.

    Raises CodexBackendError on incomplete discovery. Account auth remains
    `unknown` when login status cannot be positively identified.
    """
    executable = _executable(executable)
    timeout = _positive_timeout(timeout)
    deadline = time.monotonic() + timeout
    auth_mode = _auth_mode(executable, min(timeout / 3, 5))
    inbox: queue.Queue = queue.Queue(maxsize=128)
    stop = threading.Event()
    proc = None
    reader = None
    try:
        proc = subprocess.Popen(
            [executable, "app-server", "-c", 'model_provider="openai"'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            shell=False,
        )
        reader = threading.Thread(
            target=_read_stdout, args=(proc.stdout, inbox, stop), daemon=True
        )
        reader.start()

        def send(message: dict) -> None:
            try:
                proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                proc.stdin.flush()
            except (OSError, ValueError):
                raise CodexBackendError("app_server_write_failed") from None

        def request(request_id: int, method: str, params: dict) -> dict:
            send({"id": request_id, "method": method, "params": params})
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CodexBackendError("app_server_timeout")
                try:
                    message = inbox.get(timeout=remaining)
                except queue.Empty:
                    raise CodexBackendError("app_server_timeout") from None
                if message is _EOF:
                    raise CodexBackendError("app_server_closed")
                if isinstance(message, CodexBackendError):
                    raise message
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    raise CodexBackendError("app_server_rpc_error")
                result = message.get("result")
                if not isinstance(result, dict):
                    raise CodexBackendError("invalid_app_server_result")
                return result

        request(1, "initialize", {
            "clientInfo": {
                "name": "luna_decisions_router", "title": "Luna Decisions Router", "version": "2.6.0"
            }
        })
        send({"method": "initialized", "params": {}})
        models: dict[str, dict] = {}
        cursor = None
        seen_cursors: set[str] = set()
        request_id = 2
        while True:
            params: dict = {"limit": 20, "includeHidden": False}
            if cursor is not None:
                params["cursor"] = cursor
            result = request(request_id, "model/list", params)
            request_id += 1
            entries = result.get("data")
            if not isinstance(entries, list):
                raise CodexBackendError("invalid_model_list")
            for entry in entries:
                model = _normalize_model(entry)
                if model is not None:
                    previous = models.get(model["id"])
                    if previous is not None and previous != model:
                        raise CodexBackendError("conflicting_model_catalog_entries")
                    models[model["id"]] = model
            cursor = result.get("nextCursor")
            if cursor is None:
                break
            if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                raise CodexBackendError("invalid_model_list_cursor")
            seen_cursors.add(cursor)
            if len(seen_cursors) > 100:
                raise CodexBackendError("model_list_page_limit")
        return {
            "source": "codex.model/list",
            "discovered_at": datetime.now(timezone.utc).isoformat(),
            "simulation": False,
            "auth_mode": auth_mode,
            "models": list(models.values()),
        }
    except CodexBackendError:
        raise
    except OSError:
        raise CodexBackendError("codex_unavailable") from None
    finally:
        stop.set()
        if proc is not None:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except (OSError, ValueError):
                    pass
            _stop_process(proc)
        if reader is not None:
            reader.join(timeout=1)
        if proc is not None and proc.stdout is not None and (
            reader is None or not reader.is_alive()
        ):
            proc.stdout.close()


ROUTER_MCP_SERVER = "luna_router_v270"


def _router_mcp_disable() -> list[str]:
    """Hide the router's own MCP tools from a dispatched worker (no recursion).

    Codex rejects an override for an undefined server, so only add it when the
    active config.toml registers that server.
    """
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        import tomllib
        with open(home / "config.toml", "rb") as handle:
            servers = tomllib.load(handle).get("mcp_servers", {})
    except (OSError, ValueError, ImportError):
        return []
    if not isinstance(servers, dict) or ROUTER_MCP_SERVER not in servers:
        return []
    return ["-c", f"mcp_servers.{ROUTER_MCP_SERVER}.enabled=false"]


def build_command(
    model: str,
    effort: str,
    project: Path,
    sandbox: str = "workspace-write",
    executable: str = "codex",
) -> list[str]:
    """Create argv only. The task is always supplied separately through stdin."""
    executable = _executable(executable)
    if not isinstance(model, str) or not _IDENTIFIER.fullmatch(model):
        raise ValueError("invalid model identifier")
    if not isinstance(effort, str) or not _EFFORT.fullmatch(effort):
        raise ValueError("invalid effort identifier")
    if sandbox not in SAFE_SANDBOXES:
        raise ValueError("sandbox must be read-only or workspace-write")
    path = Path(project).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise ValueError("project must be an existing directory")
    return [
        # The caller names the project explicitly; non-git folders must not stall.
        executable, "exec", "--json", "--color", "never", "--skip-git-repo-check",
        "--model", model, "--sandbox", sandbox, "--cd", str(path),
        "-c", 'model_provider="openai"',
        "-c", 'forced_login_method="chatgpt"',
        # Unattended worker: never pause for approval; the sandbox still applies.
        "-c", 'approval_policy="never"',
        "-c", f'model_reasoning_effort="{effort}"',
        *_router_mcp_disable(),
        "-",
    ]


def _parse_task_output(output: Any) -> dict:
    """Read bounded JSONL tail; expose event metadata, not raw tool/reasoning text."""
    output.flush()
    output.seek(0, 2)
    size = output.tell()
    offset = max(0, size - MAX_OUTPUT_TAIL)
    thread_id = None
    if offset:
        output.seek(0)
        for line in output.read(65536).splitlines():
            try:
                item = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if isinstance(item, dict) and item.get("type") == "thread.started":
                value = item.get("thread_id")
                if isinstance(value, str):
                    thread_id = value
                    break
    output.seek(offset)
    raw = output.read(MAX_OUTPUT_TAIL)
    if offset:
        raw = raw.partition(b"\n")[2]
    events: deque = deque(maxlen=MAX_EVENTS)
    terminal = None
    worker_message = ""
    malformed = 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError):
            malformed += 1
            continue
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            malformed += 1
            continue
        kind = event["type"]
        brief: dict = {"type": kind}
        if kind == "thread.started" and isinstance(event.get("thread_id"), str):
            thread_id = event["thread_id"]
            brief["thread_id"] = thread_id
        if kind in {"turn.completed", "turn.failed"}:
            terminal = kind
        usage = event.get("usage")
        if isinstance(usage, dict):
            brief["usage"] = {
                k: v for k, v in usage.items()
                if isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool)
            }
        item = event.get("item")
        if isinstance(item, dict):
            brief["item"] = {
                k: item[k] for k in ("id", "type", "status") if isinstance(item.get(k), str)
            }
            if kind == "item.completed" and item.get("type") == "agent_message":
                value = item.get("text")
                if isinstance(value, str):
                    worker_message = value[-MAX_WORKER_MESSAGE:]
        events.append(brief)
    return {
        "terminal": terminal,
        "thread_id": thread_id,
        "worker_message": worker_message,
        "events": list(events),
        "output_truncated": bool(offset),
        "malformed_output_lines": malformed,
    }


def login_status(executable: str = "codex", timeout: float = 5) -> dict:
    """Separate, read-only positive auth check used after model/list failures."""
    return {"auth_mode": _auth_mode(_executable(executable), _positive_timeout(timeout))}


def recently_discovered(runtime: dict, seconds: int = 90) -> bool:
    """Reuse a verified model/list fetched in this same CLI invocation.

    This alone is not entitlement proof; the actual Codex exec outcome is
    reported separately. Caller must only set use_recent_discovery for its
    direct result of discover(), never for user-provided runtime JSON.
    """
    if not isinstance(runtime, dict) or runtime.get("source") != "codex.model/list":
        return False
    if runtime.get("simulation") is not False or runtime.get("auth_mode") != "chatgpt":
        return False
    stamp = runtime.get("discovered_at")
    if not isinstance(stamp, str):
        return False
    try:
        dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()
        return 0 <= age <= seconds
    except (ValueError, AttributeError, OverflowError):
        return False


def build_default_command(
    project: Path, sandbox: str = "workspace-write", executable: str = "codex"
) -> list[str]:
    """Use the authenticated Codex CLI default; no unverified model pin."""
    executable = _executable(executable)
    if sandbox not in SAFE_SANDBOXES:
        raise ValueError("sandbox must be read-only or workspace-write")
    path = Path(project).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise ValueError("project must be an existing directory")
    return [executable, "exec", "--json", "--color", "never", "--skip-git-repo-check",
            "--sandbox", sandbox, "--cd", str(path),
            "-c", 'model_provider="openai"',
            "-c", 'forced_login_method="chatgpt"',
            "-c", 'approval_policy="never"', *_router_mcp_disable(), "-"]


def run_default_task(
    task_text: str, project: Path, runtime: dict,
    sandbox: str = "workspace-write", timeout: float = 1800,
    executable: str = "codex",
) -> dict:
    """Run a real Codex turn when model/list fails, without claiming a model switch.

    Never retries internally, skips model selection entirely and requires a
    positive ChatGPT login status. A missing terminal event is not success.
    """
    result = {"requested_model": None, "requested_effort": None,
              "actual_model": None, "actual_effort": None,
              "selection_source": "codex_cli_default_unverified",
              "status": "failed", "exit_code": None, "thread_id": None,
              "worker_message": "", "events": [], "worker_started": False}
    if not isinstance(runtime, dict) or runtime.get("simulation") is not False:
        return {**result, "error": "simulation_or_unverified_runtime_blocked"}
    if not isinstance(task_text, str) or not task_text.strip() or "\x00" in task_text:
        return {**result, "error": "invalid_task_text"}
    try:
        timeout = _positive_timeout(timeout)
        command = build_default_command(project, sandbox, executable)
    except (OSError, ValueError, TypeError):
        return {**result, "error": "invalid_execution_request"}
    # Positive CLI auth is necessary; a failure or unexpected status is not
    # permission to attempt an unbounded API-key or unknown-provider worker.
    if login_status(executable=executable, timeout=min(5, timeout))["auth_mode"] != "chatgpt":
        return {**result, "error": "chatgpt_subscription_auth_required"}
    proc = None
    timed_out = False
    with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(mode="w+b") as stderr_file:
        try:
            proc = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=stdout_file, stderr=stderr_file,
                text=True, encoding="utf-8", errors="replace", shell=False,
                env={**os.environ, "LUNA_ROUTER_WORKER": "1"},
            )
            result["worker_started"] = True
            try:
                proc.communicate(input=task_text, timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _stop_process(proc)
            result["exit_code"] = proc.returncode
            parsed = _parse_task_output(stdout_file)
            terminal = parsed.pop("terminal")
            result.update(parsed)
            if timed_out:
                result["status"] = "unknown"
                result["error"] = "execution_timeout_do_not_retry_automatically"
            elif proc.returncode != 0 or terminal == "turn.failed":
                result["status"] = "failed"
                result["error"] = "codex_turn_failed"
            elif terminal == "turn.completed":
                result["status"] = "turn_completed"
            else:
                result["status"] = "unknown"
                result["error"] = "missing_terminal_event_do_not_retry_automatically"
        except OSError:
            if proc is not None:
                result["status"] = "unknown"
                result["error"] = "execution_interrupted_do_not_retry_automatically"
            else:
                result["error"] = "codex_launch_failed"
        finally:
            if proc is not None:
                _stop_process(proc)
                if proc.stdin is not None:
                    try:
                        proc.stdin.close()
                    except (OSError, ValueError):
                        pass
    return result


def run_task(
    task_text: str,
    model: str,
    effort: str,
    project: Path,
    runtime: dict,
    sandbox: str = "workspace-write",
    timeout: float = 1800,
    executable: str = "codex",
    use_recent_discovery: bool = False,
) -> dict:
    """Freshly verify subscription auth/capabilities, then run exactly one turn.

    `runtime` is accepted for caller compatibility and simulation protection;
    its model or auth claims never replace fresh discovery. `turn_completed`
    means the model turn ended, not that an acceptance test passed.
    """
    result: dict = {
        "requested_model": model,
        "requested_effort": effort,
        "status": "failed",
        "exit_code": None,
        "thread_id": None,
        "worker_message": "",
        "events": [],
        "actual_model": None,
        "actual_effort": None,
        "worker_started": False,
    }

    def fail(code: str) -> dict:
        result["error"] = code
        return result

    if not isinstance(runtime, dict) or runtime.get("simulation") is not False:
        return fail("simulation_or_unverified_runtime_blocked")
    if not isinstance(task_text, str) or not task_text.strip() or "\x00" in task_text:
        return fail("invalid_task_text")
    try:
        timeout = _positive_timeout(timeout)
        command = build_command(model, effort, project, sandbox, executable)
    except (ValueError, TypeError, OSError):
        return fail("invalid_execution_request")
    try:
        if use_recent_discovery and recently_discovered(runtime):
            fresh = runtime
            result["capability_source"] = "recent_app_server_snapshot"
        else:
            fresh = discover(executable=executable, timeout=min(20, timeout))
            result["capability_source"] = "fresh_app_server_query"
    except (CodexBackendError, ValueError) as exc:
        result["diagnostic_code"] = str(exc)  # CodexBackendError is sanitized
        return fail("codex_discovery_failed")
    if fresh.get("simulation") is not False or fresh.get("source") != "codex.model/list":
        return fail("unverified_discovery_blocked")
    if fresh.get("auth_mode") != "chatgpt":
        return fail("chatgpt_subscription_auth_required")
    available = [entry for entry in fresh.get("models", []) if isinstance(entry, dict)]
    selected = next((entry for entry in available if entry.get("id") == model), None)
    if selected is None:
        return fail("model_not_available")
    if effort not in selected.get("efforts", []):
        return fail("effort_not_supported")
    proc = None
    timed_out = False
    with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(mode="w+b") as stderr_file:
        try:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=stdout_file,
                stderr=stderr_file,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                env={**os.environ, "LUNA_ROUTER_WORKER": "1"},
            )
            result["worker_started"] = True
            try:
                proc.communicate(input=task_text, timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _stop_process(proc)
            result["exit_code"] = proc.returncode
            parsed = _parse_task_output(stdout_file)
            terminal = parsed.pop("terminal")
            result.update(parsed)
            if timed_out:
                result["status"] = "unknown"
                result["error"] = "execution_timeout_do_not_retry_automatically"
            elif proc.returncode != 0 or terminal == "turn.failed":
                result["status"] = "failed"
                result["error"] = "codex_turn_failed"
            elif terminal == "turn.completed":
                result["status"] = "turn_completed"
            else:
                result["status"] = "unknown"
                result["error"] = "missing_terminal_event_do_not_retry_automatically"
        except OSError:
            if proc is not None:
                result["status"] = "unknown"
                return fail("execution_interrupted_do_not_retry_automatically")
            return fail("codex_launch_failed")
        finally:
            if proc is not None:
                _stop_process(proc)
                if proc.stdin is not None:
                    try:
                        proc.stdin.close()
                    except (OSError, ValueError):
                        pass
    return result
