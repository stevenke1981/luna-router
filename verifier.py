"""Explicit independent task-acceptance check, executed without a shell.

No check means no proven success/failure; a model's completion is not QA.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile


def read_check(path: str | Path, default_timeout: int = 300) -> dict:
    """User-authored verification contract: {argv:[...],timeout_seconds:...}."""
    if not isinstance(default_timeout, int) or isinstance(default_timeout, bool) or not 1 <= default_timeout <= 900:
        raise ValueError("invalid default verifier timeout")
    obj = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(obj, dict) or set(obj) - {"argv", "timeout_seconds"}:
        raise ValueError("verifier contract must be an object with argv and optional timeout_seconds")
    argv = obj.get("argv")
    if not isinstance(argv, list) or not (1 <= len(argv) <= 64) or any(
        not isinstance(part, str) or not part or "\x00" in part or len(part) > 4096 for part in argv
    ):
        raise ValueError("verifier argv must contain 1-64 nonempty strings")
    timeout = obj.get("timeout_seconds", default_timeout)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 1 <= timeout <= 900:
        raise ValueError("verifier timeout_seconds must be between 1 and 900")
    return {"argv": list(argv), "timeout_seconds": float(timeout)}


def run_check(contract: dict, project: str | Path) -> dict:
    """Use return code only; never return stdout/stderr or confidential paths."""
    path = Path(project).resolve(strict=True)
    if not path.is_dir():
        raise ValueError("project must be an existing directory")
    argv = contract["argv"]
    timeout = contract["timeout_seconds"]
    digest = hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()
    with tempfile.TemporaryFile(mode="w+b") as output:
        try:
            result = subprocess.run(
                argv, cwd=path, stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.STDOUT,
                shell=False, timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired:
            return {"outcome": "unknown", "evidence_kind": "worker", "reason": "verifier_timeout_no_retry", "check_id": digest}
        except OSError:
            return {"outcome": "blocked", "evidence_kind": "worker", "reason": "verifier_cannot_launch", "check_id": digest}
        output.seek(0)
        content_hash = hashlib.sha256()
        for _ in range(128):
            block = output.read(65536)
            if not block:
                break
            content_hash.update(block)
        if result.returncode is None or result.returncode < 0 or result.returncode in (126, 127):
            return {"outcome": "blocked", "evidence_kind": "worker", "reason": "verifier_execution_invalid", "exit_code": result.returncode, "check_id": digest}
        return {"outcome": "passed" if result.returncode == 0 else "failed", "evidence_kind": "verifier", "exit_code": result.returncode,
                "check_id": digest, "evidence_sha256": content_hash.hexdigest(), "stdout_hash_is_bounded": True}
