# receptionist/crm/status.py
"""Ask the CRM which mode to answer in. Fails open: any problem means "normal"."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import httpx

from receptionist.crm.result import sign

logger = logging.getLogger("receptionist")
MODES = ("normal", "message_only", "off")
STATUS_TIMEOUT = 2.0
WEBHOOK_SUFFIX = "crm.channels.webhooks.voice"
STATUS_SUFFIX = "crm.channels.webhooks.voice_status"


async def fetch_mode(config: Any) -> str:
    crm = getattr(config, "crm", None)
    if crm is None:
        return "normal"
    extra = {"component": "crm.status", "business_name": getattr(config.business, "name", "")}
    secret = os.environ.get(crm.secret_env, "").strip()
    if not secret:
        logger.warning("CRM mode check skipped: env %s is not set; answering normally", crm.secret_env, extra=extra)
        return "normal"
    if not crm.url.endswith(WEBHOOK_SUFFIX):
        logger.warning("CRM mode check skipped: crm.url does not end with %s", WEBHOOK_SUFFIX, extra=extra)
        return "normal"
    url = crm.url[: -len(WEBHOOK_SUFFIX)] + STATUS_SUFFIX
    body = json.dumps({"v": 1, "to": crm.business_number}, separators=(",", ":")).encode()
    headers = {"Content-Type": "application/json", "X-CRM-Signature": sign(body, secret)}
    async def _request() -> httpx.Response:
        async with httpx.AsyncClient(timeout=STATUS_TIMEOUT) as client:
            return await client.post(url, content=body, headers=headers)

    try:
        # httpx timeouts are per phase; wait_for is the hard ceiling on the whole check.
        response = await asyncio.wait_for(_request(), STATUS_TIMEOUT)
        if response.status_code != 200:
            logger.warning("CRM mode check got HTTP %d; answering normally", response.status_code, extra=extra)
            return "normal"
        data = response.json()
        mode = data.get("mode") if isinstance(data, dict) else None
    except (asyncio.TimeoutError, Exception) as e:
        logger.warning("CRM mode check failed (%s); answering normally", type(e).__name__, extra=extra)
        return "normal"
    if mode not in MODES:
        logger.warning("CRM mode check returned an unknown mode; answering normally", extra=extra)
        return "normal"
    return mode
