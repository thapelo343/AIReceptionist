# receptionist/crm/delivery.py
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from receptionist.crm.result import sign
from receptionist.messaging.failures import resolve_failures_dir
from receptionist.messaging.retry import RetryPolicy, retry_with_backoff

logger = logging.getLogger("receptionist")
SIGNATURE_HEADER = "X-CRM-Signature"


class _PermanentHTTPError(Exception):
    """4xx (except 408/425/429) — the CRM rejected the call result; retrying won't help."""


async def deliver_call_result(config: Any, payload: dict, *, initial_delay: float = 1.0) -> bool:
    """POST one signed CallResult to the CRM. Never raises; archives to .failures/ when delivery fails."""
    extra = {"call_id": payload.get("call_id"), "business_name": config.business.name, "component": "crm.delivery"}
    secret = os.environ.get(config.crm.secret_env, "").strip()
    if not secret:
        logger.error("CRM delivery skipped: env %s is not set", config.crm.secret_env, extra=extra)
        _archive(config, payload, f"env {config.crm.secret_env} is not set")
        return False

    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    headers = {"Content-Type": "application/json", SIGNATURE_HEADER: sign(body, secret)}

    async def _post() -> None:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(config.crm.url, content=body, headers=headers)
        if 400 <= resp.status_code < 500 and resp.status_code not in {408, 425, 429}:
            raise _PermanentHTTPError(f"HTTP {resp.status_code}")
        resp.raise_for_status()

    try:
        await retry_with_backoff(
            _post,
            RetryPolicy(max_attempts=3, initial_delay=initial_delay, factor=2.0),
            is_transient=lambda e: not isinstance(e, _PermanentHTTPError),
        )
    except Exception as e:
        logger.error("CRM delivery failed: %s", e, extra=extra)
        _archive(config, payload, f"{type(e).__name__}: {e}")
        return False
    logger.info("CRM delivery ok", extra=extra)
    return True


def _archive(config: Any, payload: dict, error: str) -> None:
    try:
        folder = resolve_failures_dir(config.messages.channels, config.business.name)
        folder.mkdir(parents=True, exist_ok=True)
        record = {"payload": payload, "error": error, "at": datetime.now(timezone.utc).isoformat()}
        (folder / f"crm-{payload.get('call_id', 'unknown')}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    except Exception:
        logger.exception("CRM delivery: could not archive failed call result", extra={"component": "crm.delivery"})
