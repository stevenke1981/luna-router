"""Bounded retries for routing decisions only; never replay a worker."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import openrouter_backend as orb

TRANSIENT_HTTP = frozenset({500, 502, 503, 504})


def retry_delay(retry_after, attempt):
    delay = float(attempt)
    if retry_after:
        try:
            delay = float(retry_after) if retry_after.isdigit() else (
                parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)
            ).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    # Never shorten a server's longer backoff and retry too soon.
    return max(0.0, delay) if delay <= 5 else None


def decide_with_retry(body, record, policy, ledger, job, *, max_attempts=1,
                      attempts=None, emit=None, sleep=None):
    if type(max_attempts) is not int or not 1 <= max_attempts <= 3:
        raise ValueError('decision_attempts_must_be_between_one_and_three')
    attempts = attempts if attempts is not None else []
    sleep = sleep or time.sleep
    for number in range(1, max_attempts + 1):
        # Every possibly billable request consumes the same job's quota.
        ledger.reserve(job, 'decision')
        attempt = {'attempt': number, 'status': 'requested'}
        attempts.append(attempt)
        if emit:
            emit('decision_attempt_started', attempt=number)
        try:
            result = orb.decide(body, record, policy)
        except orb.APIError as exc:
            attempt.update(status='failed', http_status=exc.status)
            delay = retry_delay(exc.retry_after, number)
            retry = exc.status in TRANSIENT_HTTP and number < max_attempts and delay is not None
            attempt['will_retry'] = retry
            if emit:
                emit('decision_attempt_failed', **attempt)
            if not retry:
                raise
            attempt['wait_seconds'] = delay
            sleep(delay)
        else:
            attempt['status'] = 'response_received'
            if emit:
                emit('decision_attempt_completed', attempt=number)
            return result
    raise AssertionError('unreachable')
