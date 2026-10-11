from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace

import httpx
import pytest
import respx
from httpx import Response

from receptionist.crm.status import fetch_mode

URL = "https://crm.example.com/api/method/crm.channels.webhooks.voice"
STATUS_URL = "https://crm.example.com/api/method/crm.channels.webhooks.voice_status"


def _config(**crm):
    return SimpleNamespace(
        business=SimpleNamespace(name="Acme"),
        crm=SimpleNamespace(**{"url": URL, "secret_env": "CRM_SECRET_TEST", "business_number": "+27105550100", **crm}),
    )


@pytest.mark.asyncio
async def test_no_crm_block_is_normal():
    assert await fetch_mode(SimpleNamespace(crm=None)) == "normal"


@pytest.mark.asyncio
@respx.mock
async def test_signed_request_and_modes(monkeypatch):
    monkeypatch.setenv("CRM_SECRET_TEST", "s3cret")
    for mode in ("normal", "message_only", "off"):
        route = respx.post(STATUS_URL).mock(return_value=Response(200, json={"mode": mode, "minutes_used": 1, "minutes_limit": 2}))
        assert await fetch_mode(_config()) == mode
        request = route.calls.last.request
        assert json.loads(request.content) == {"v": 1, "to": "+27105550100"}
        expected = "sha256=" + hmac.new(b"s3cret", request.content, hashlib.sha256).hexdigest()
        assert request.headers["x-crm-signature"] == expected


@pytest.mark.asyncio
@respx.mock
async def test_fails_open(monkeypatch, caplog):
    monkeypatch.setenv("CRM_SECRET_TEST", "s3cret")
    for response in (Response(403), Response(500), Response(200, text="<html>"), Response(200, json={"mode": "weird"}), Response(200, json=[1])):
        respx.post(STATUS_URL).mock(return_value=response)
        assert await fetch_mode(_config()) == "normal"
    respx.post(STATUS_URL).mock(side_effect=httpx.ConnectTimeout("slow"))
    assert await fetch_mode(_config()) == "normal"
    assert "s3cret" not in caplog.text


@pytest.mark.asyncio
async def test_missing_secret_or_odd_url_is_normal(monkeypatch, caplog):
    monkeypatch.delenv("CRM_SECRET_TEST", raising=False)
    assert await fetch_mode(_config()) == "normal"
    monkeypatch.setenv("CRM_SECRET_TEST", "s3cret")
    assert await fetch_mode(_config(url="https://crm.example.com/somewhere-else")) == "normal"
    assert "CRM_SECRET_TEST" in caplog.text
