"""OpenRouter request counters/cache and dormant OpenAI-direct cost ledger.

OpenRouter paid Decisions use only count and cache tables. Monetary reservations
are exclusively accessed through cost_control.OpenAIExpenseGuard for any future
direct OpenAI API backend; the two backends have separate SQLite database paths.
"""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_DOWN, localcontext
import json
import math
import sqlite3
import time
from pathlib import Path


NANO_USD = 1_000_000_000
_USD_UNIT = Decimal("0.000000001")
_MAX_USD = Decimal("9223372036.854775807")
_KINDS = frozenset({"decision", "free_worker"})


class BudgetExceeded(RuntimeError):
    pass


def _money(value, label: str) -> int:
    """Convert an exact, finite, nonnegative amount into SQLite-safe nanoUSD."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError(f"{label} must be a finite nonnegative USD amount")
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount > _MAX_USD:
            raise ValueError(f"{label} is outside the supported USD range")
        with localcontext() as context:
            context.prec = 40
            exact = amount.quantize(_USD_UNIT, rounding=ROUND_DOWN)
            if exact != amount:
                raise ValueError(f"{label} must be exactly representable in nanoUSD")
            return int(exact * NANO_USD)
    except (InvalidOperation, OverflowError):
        raise ValueError(f"{label} must be a finite nonnegative USD amount") from None


def _usd(nano: int) -> str:
    return f"{nano // NANO_USD}.{nano % NANO_USD:09d}"


def _timestamp(value: float | None) -> float:
    value = time.time() if value is None else value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("now must be a finite nonnegative Unix timestamp")
    if not math.isfinite(value) or not 0 <= value <= 253402300799:
        raise ValueError("now must be a finite nonnegative Unix timestamp")
    return float(value)


def _day(timestamp: float) -> int:
    return int(timestamp // 86400) * 86400


def _job(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("job must be a non-empty string")
    return value


def _count_limit(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


class Ledger:
    def __init__(self, path: Path, policy: dict):
        if not isinstance(policy, dict):
            raise ValueError("policy must be an object")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.policy = dict(policy)
        self.minute_limit = _count_limit(policy.get("requests_per_minute", 18), "requests_per_minute")
        self.job_limit = _count_limit(policy.get("requests_per_job", 2), "requests_per_job")
        # Old daily settings remain a fallback, with separate category counters.
        self.free_daily_limit = _count_limit(
            policy.get("free_requests_per_utc_day", policy.get("requests_per_utc_day", 40)),
            "free_requests_per_utc_day",
        )
        self.decision_daily_limit = _count_limit(
            policy.get("decision_requests_per_utc_day", policy.get("requests_per_utc_day", 1000)),
            "decision_requests_per_utc_day",
        )
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS requests (ts REAL, job TEXT, kind TEXT)")
            db.execute("CREATE INDEX IF NOT EXISTS requests_time ON requests(ts)")
            db.execute("CREATE TABLE IF NOT EXISTS decisions (key TEXT PRIMARY KEY, ts REAL, body TEXT)")
            db.execute("""CREATE TABLE IF NOT EXISTS paid_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                utc_day INTEGER NOT NULL,
                job TEXT NOT NULL,
                reserved_nano INTEGER NOT NULL CHECK (reserved_nano >= 0),
                actual_nano INTEGER CHECK (actual_nano >= 0),
                status TEXT NOT NULL CHECK (status IN ('reserved', 'unknown', 'settled')),
                updated_at REAL NOT NULL,
                cost_overrun INTEGER NOT NULL DEFAULT 0 CHECK (cost_overrun IN (0, 1))
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS paid_decisions_day ON paid_decisions(utc_day)")

    @contextmanager
    def connect(self):
        # sqlite3.Connection.__exit__ commits/rolls back but does not close.
        # Close deterministically to avoid descriptor leaks on long-lived agents.
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _check_requests(self, db, job: str, kind: str, now: float):
        checks = [
            ("SELECT COUNT(*) FROM requests WHERE ts > ?", (now - 60,),
             self.minute_limit, "requests_per_minute"),
            ("SELECT COUNT(*) FROM requests WHERE job = ?", (job,),
             self.job_limit, "requests_per_job"),
        ]
        day_start = _day(now)
        if kind == "decision":
            checks.append((
                "SELECT COUNT(*) FROM requests WHERE ts >= ? AND ts < ? AND kind = 'decision'",
                (day_start, day_start + 86400), self.decision_daily_limit,
                "decision_requests_per_utc_day",
            ))
        else:
            # Preserve old worker records in free counts; new writes use free_worker.
            checks.append((
                "SELECT COUNT(*) FROM requests WHERE ts >= ? AND ts < ? AND kind IN ('free_worker', 'worker')",
                (day_start, day_start + 86400), self.free_daily_limit,
                "free_requests_per_utc_day",
            ))
        for sql, parameters, limit, setting in checks:
            if db.execute(sql, parameters).fetchone()[0] >= limit:
                raise BudgetExceeded(setting)

    @staticmethod
    def _record_request(db, job: str, kind: str, now: float):
        db.execute("INSERT INTO requests VALUES (?, ?, ?)", (now, job, kind))
        db.execute("DELETE FROM requests WHERE ts < ?", (now - 7 * 86400,))

    def reserve(self, job: str, kind: str, now: float | None = None):
        """Reserve request count only. Paid decisions MUST use reserve_decision.

        The decision kind here is for legacy count-only callers; it does not
        authorize or reserve any paid inference cost.
        """
        job = _job(job)
        if not isinstance(kind, str) or kind not in _KINDS:
            raise ValueError("kind must be decision or free_worker")
        now = _timestamp(now)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_requests(db, job, kind, now)
            # Count before transmission: an uncertain transport outcome must not be replayed.
            self._record_request(db, job, kind, now)

    @staticmethod
    def _paid_totals(db, utc_day: int) -> dict:
        rows = db.execute(
            "SELECT reserved_nano, actual_nano, status, cost_overrun FROM paid_decisions WHERE utc_day = ?",
            (utc_day,),
        ).fetchall()
        # Python ints can report an overrun beyond SQLite SUM's int64 limit.
        reserved = sum(row[0] for row in rows if row[1] is None)
        settled = sum(row[1] for row in rows if row[1] is not None)
        return {
            "reserved_nano": reserved,
            "settled_nano": settled,
            "charged_or_reserved_nano": reserved + settled,
            "cost_overrun": any(row[3] for row in rows),
            "reservation_count": len(rows),
            "settled_count": sum(row[1] is not None for row in rows),
            "unknown_count": sum(row[2] == "unknown" for row in rows),
        }

    def reserve_decision(self, job: str, decision_policy: dict, now: float | None = None) -> int:
        """Atomically reserve one paid request and its full maximum USD cost."""
        job = _job(job)
        now = _timestamp(now)
        if not isinstance(decision_policy, dict):
            raise ValueError("decision_policy must be an object")
        maximum = _money(decision_policy.get("max_request_cost_usd"), "max_request_cost_usd")
        daily = _money(decision_policy.get("max_daily_cost_usd"), "max_daily_cost_usd")
        if maximum == 0:
            raise ValueError("max_request_cost_usd must be greater than zero")
        utc_day = _day(now)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            totals = self._paid_totals(db, utc_day)
            if totals["cost_overrun"]:
                raise BudgetExceeded("decision_cost_overrun")
            if totals["charged_or_reserved_nano"] + maximum > daily:
                raise BudgetExceeded("max_daily_cost_usd")
            self._check_requests(db, job, "decision", now)
            cursor = db.execute(
                """INSERT INTO paid_decisions
                   (ts, utc_day, job, reserved_nano, actual_nano, status, updated_at, cost_overrun)
                   VALUES (?, ?, ?, ?, NULL, 'reserved', ?, 0)""",
                (now, utc_day, job, maximum, now),
            )
            reservation_id = cursor.lastrowid
            self._record_request(db, job, "decision", now)
        return int(reservation_id)

    @staticmethod
    def _reservation_result(row) -> dict:
        reservation_id, utc_day, reserved, actual, status, overrun = row
        return {
            "reservation_id": reservation_id,
            "utc_day": datetime.fromtimestamp(utc_day, timezone.utc).date().isoformat(),
            "status": status,
            "max_request_cost_usd": _usd(reserved),
            "reserved_usd": _usd(reserved if actual is None else 0),
            "actual_cost_usd": None if actual is None else _usd(actual),
            "charged_or_reserved_usd": _usd(reserved if actual is None else actual),
            "released_usd": _usd(0 if actual is None else max(0, reserved - actual)),
            "cost_overrun": bool(overrun),
        }

    def settle_decision(self, reservation_id: int, actual_cost_usd=None, now: float | None = None) -> dict:
        """Record actual cost, or retain the full reservation when it is unknown.

        Unknown cost can later become known. A known cost cannot change or become
        None. Repeated equal costs are idempotent. Commit an overrun before raising
        BudgetExceeded. Charge costs to the reservation's original UTC day.
        """
        if isinstance(reservation_id, bool) or not isinstance(reservation_id, int) or reservation_id <= 0:
            raise ValueError("reservation_id must be a positive integer")
        now = _timestamp(now)
        actual = None if actual_cost_usd is None else _money(actual_cost_usd, "actual_cost_usd")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT id, utc_day, reserved_nano, actual_nano, status, cost_overrun
                   FROM paid_decisions WHERE id = ?""",
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise ValueError("unknown decision reservation")
            if row[3] is not None:
                if actual != row[3]:
                    raise ValueError("decision_settlement_conflict")
            elif actual is None:
                if row[4] != "unknown":
                    db.execute(
                        "UPDATE paid_decisions SET status = 'unknown', updated_at = ? WHERE id = ?",
                        (now, reservation_id),
                    )
                row = (row[0], row[1], row[2], None, "unknown", row[5])
            else:
                overrun = int(actual > row[2])
                db.execute(
                    """UPDATE paid_decisions
                       SET actual_nano = ?, status = 'settled', updated_at = ?, cost_overrun = ?
                       WHERE id = ?""",
                    (actual, now, overrun, reservation_id),
                )
                row = (row[0], row[1], row[2], actual, "settled", overrun)
            result = self._reservation_result(row)
        # Outside the transaction: real overrun cost must not be rolled back.
        if result["cost_overrun"]:
            raise BudgetExceeded("decision_cost_overrun")
        return result

    def paid_daily_summary(self, now: float | None = None) -> dict:
        """Separate outstanding reservations from known charges for one UTC day."""
        utc_day = _day(_timestamp(now))
        with self.connect() as db:
            totals = self._paid_totals(db, utc_day)
        return {
            "utc_day": datetime.fromtimestamp(utc_day, timezone.utc).date().isoformat(),
            "reserved_usd": _usd(totals["reserved_nano"]),
            "settled_usd": _usd(totals["settled_nano"]),
            "charged_or_reserved_usd": _usd(totals["charged_or_reserved_nano"]),
            "cost_overrun": totals["cost_overrun"],
            "reservation_count": totals["reservation_count"],
            "settled_count": totals["settled_count"],
            "unsettled_count": totals["reservation_count"] - totals["settled_count"],
            "unknown_count": totals["unknown_count"],
        }

    def get(self, key: str, now: float | None = None):
        now = time.time() if now is None else now
        with self.connect() as db:
            row = db.execute("SELECT ts, body FROM decisions WHERE key = ?", (key,)).fetchone()
        if not row or now < row[0] or now - row[0] > self.policy["decision_cache_seconds"]:
            return None
        return json.loads(row[1])

    def put(self, key: str, body: dict, now: float | None = None):
        now = time.time() if now is None else now
        encoded = json.dumps(body, ensure_ascii=False, allow_nan=False)
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO decisions VALUES (?, ?, ?)", (key, now, encoded))
            db.execute("DELETE FROM decisions WHERE ts < ?", (now - self.policy["decision_cache_seconds"],))
