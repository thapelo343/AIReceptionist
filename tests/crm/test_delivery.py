from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest
import respx
from httpx import Response

from receptionist.config import FileChannel
from receptionist.crm.delivery import deliver_call_result

URL = "https://crm.example.com/api/method/crm.channels.webhooks.voice"


def _config(tmp_path):
    return SimpleNamespace(
        business=SimpleNamespace(name="Acme Dental"),
        messages=SimpleNamespace(channels=[FileChannel(type="file", file_path=str(tmp_path / "messages"))]),
        crm=SimpleNamespace(url=URL, secret_env="CRM_SECRET_TEST"),
    )


PAYLOAD = {"v": 1, "call_id": "lk-9", "outcome": "hangup"}


@pytest.mark.asyncio
@respx.mock
async def test_posts_signed_body(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_SECRET_TEST", "s3cret")
    route = respx.post(URL).mock(return_value=Response(200))
    assert await deliver_call_result(_config(tmp_path), PAYLOAD, initial_delay=0) is True
    request = route.calls.last.request
    expected = "sha256=" + hmac.new(b"s3cret", request.content, hashlib.sha256).hexdigest()
    assert request.headers["x-crm-signature"] == expected
    assert json.loads(request.content) == PAYLOAD


@pytest.mark.asyncio
@respx.mock
async def test_4xx_not_retried_and_archived(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_SECRET_TEST", "s3cret")
    route = respx.post(URL).mock(return_value=Response(403))
    assert await deliver_call_result(_config(tmp_path), PAYLOAD, initial_delay=0) is False
    assert route.call_count == 1
    archived = json.loads((tmp_path / "messages" / ".failures" / "crm-lk-9.json").read_text())
    assert archived["payload"] == PAYLOAD and "403" in archived["error"]


@pytest.mark.asyncio
@respx.mock
async def test_archives_after_retries_exhausted(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_SECRET_TEST", "s3cret")
    route = respx.post(URL).mock(return_value=Response(500))
    assert await deliver_call_result(_config(tmp_path), PAYLOAD, initial_delay=0) is False
    assert route.call_count == 3
    assert (tmp_path / "messages" / ".failures" / "crm-lk-9.json").exists()


@pytest.mark.asyncio
@respx.mock
async def test_retry_then_success(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_SECRET_TEST", "s3cret")
    route = respx.post(URL).mock(side_effect=[Response(502), Response(200)])
    assert await deliver_call_result(_config(tmp_path), PAYLOAD, initial_delay=0) is True
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_missing_secret_archived(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("CRM_SECRET_TEST", raising=False)
    assert await deliver_call_result(_config(tmp_path), PAYLOAD, initial_delay=0) is False
    assert (tmp_path / "messages" / ".failures" / "crm-lk-9.json").exists()
    assert "CRM_SECRET_TEST" in caplog.text
