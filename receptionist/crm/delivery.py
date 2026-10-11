# receptionist/crm/delivery.py
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from receptionist.crm.result import sign
from receptionist.messaging.failures import resolve_failures_dir
from receptionist.messaging.retry import RetryPolicy, retry_with_backoff

logger = logging.getLogger("receptionist")
SIGNATURE_HEADER = "X-CRM-Signature"


class _PermanentHTTPError(Exception):
    """4xx (except 408/425/429) — the CRM rejected the call result; retrying won't help."""


class _MissingSecret(Exception):
    pass


def safe_call_id(call_id: Any) -> str:
    """Filename-safe call id: keep [A-Za-z0-9._-], replace everything else with '_'."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(call_id or "")) or "unknown"


def failures_dir(config: Any) -> Path:
    return resolve_failures_dir(config.messages.channels, config.business.name)


def write_pending(config: Any, payload: dict) -> Path:
    """Write-ahead copy of the payload, so a call result survives a cancelled job (replay it later)."""
    folder = failures_dir(config)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"pending-{safe_call_id(payload.get('call_id'))}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def clear_pending(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.exception("CRM delivery: could not remove %s", path, extra={"component": "crm.delivery"})


async def deliver_call_result(config: Any, payload: dict, *, initial_delay: float = 1.0) -> bool:
    """POST one signed CallResult to the CRM.

    Never raises, except asyncio.CancelledError, which propagates and leaves
    `pending-<call_id>.json` behind for `python -m receptionist.crm.replay`.
    Any other failure archives to `crm-<call_id>.json` and returns False.
    """
    extra = {"call_id": payload.get("call_id"), "component": "crm.delivery"}
    pending: Path | None = None
    try:
        pending = write_pending(config, payload)
    except Exception:
        logger.exception("CRM delivery: could not write pending file", extra=extra)

    try:
        secret = os.environ.get(config.crm.secret_env, "").strip()
        if not secret:
            logger.error("CRM delivery skipped: env %s is not set", config.crm.secret_env, extra=extra)
            raise _MissingSecret(f"env {config.crm.secret_env} is not set")

        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        headers = {"Content-Type": "application/json", SIGNATURE_HEADER: sign(body, secret)}

        async def _post() -> None:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(config.crm.url, content=body, headers=headers)
            if 400 <= resp.status_code < 500 and resp.status_code not in {408, 425, 429}:
                raise _PermanentHTTPError(f"HTTP {resp.status_code}")
            resp.raise_for_status()

        await retry_with_backoff(
            _post,
            RetryPolicy(max_attempts=3, initial_delay=initial_delay, factor=2.0),
            is_transient=lambda e: not isinstance(e, _PermanentHTTPError),
        )
    except Exception as e:
        error = str(e) if isinstance(e, _MissingSecret) else f"{type(e).__name__}: {e}"
        logger.error("CRM delivery failed: %s", error, extra=extra)
        archive(config, payload, error)
        clear_pending(pending)
        return False
    clear_pending(pending)
    logger.info("CRM delivery ok", extra=extra)
    return True


def archive(config: Any, payload: dict, error: str) -> None:
    """Write `crm-<call_id>.json` ({payload, error, at}). Never raises."""
    try:
        folder = failures_dir(config)
        folder.mkdir(parents=True, exist_ok=True)
        record = {"payload": payload, "error": error, "at": datetime.now(timezone.utc).isoformat()}
        path = folder / f"crm-{safe_call_id(payload.get('call_id'))}.json"
        path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    except Exception:
        logger.exception("CRM delivery: could not archive failed call result", extra={"component": "crm.delivery"})
