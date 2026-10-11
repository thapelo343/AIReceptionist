from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import respx
from httpx import Response

from receptionist.config import FileChannel
from receptionist.crm.replay import replay_all, replay_file

URL = "https://crm.example.com/api/method/crm.channels.webhooks.voice"
PAYLOAD = {"v": 1, "call_id": "lk-7", "outcome": "hangup"}


def _config(tmp_path):
    return SimpleNamespace(
        business=SimpleNamespace(name="Acme Dental"),
        messages=SimpleNamespace(channels=[FileChannel(type="file", file_path=str(tmp_path / "messages"))]),
        crm=SimpleNamespace(url=URL, secret_env="CRM_SECRET_TEST"),
    )


def _failures(tmp_path):
    folder = tmp_path / "messages" / ".failures"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


@pytest.mark.asyncio
@respx.mock
async def test_replay_pending_file_succeeds_and_deletes_it(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_SECRET_TEST", "s3cret")
    route = respx.post(URL).mock(return_value=Response(200))
    path = _failures(tmp_path) / "pending-lk-7.json"
    path.write_text(json.dumps(PAYLOAD))
    ok, reason = await replay_file(_config(tmp_path), path, initial_delay=0)
    assert ok is True and reason == "ok"
    assert json.loads(route.calls.last.request.content) == PAYLOAD
    assert not list(_failures(tmp_path).glob("*.json"))


@pytest.mark.asyncio
@respx.mock
async def test_replay_403_keeps_archived_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_SECRET_TEST", "s3cret")
    respx.post(URL).mock(return_value=Response(403))
    path = _failures(tmp_path) / "crm-lk-7.json"
    path.write_text(json.dumps({"payload": PAYLOAD, "error": "HTTP 500", "at": "x"}))
    ok, reason = await replay_file(_config(tmp_path), path, initial_delay=0)
    assert ok is False and "403" in reason
    assert json.loads(path.read_text())["payload"] == PAYLOAD


@pytest.mark.asyncio
async def test_replay_skips_incomplete_record(tmp_path):
    path = _failures(tmp_path) / "crm-lk-8.json"
    path.write_text(json.dumps({"payload": {"call_id": "lk-8"}, "error": "build_call_result failed"}))
    ok, reason = await replay_file(_config(tmp_path), path, initial_delay=0)
    assert ok is False and "not a complete CallResult" in reason
    assert path.exists()


@pytest.mark.asyncio
@respx.mock
async def test_replay_all_scans_pending_and_archived(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CRM_SECRET_TEST", "s3cret")
    respx.post(URL).mock(return_value=Response(200))
    folder = _failures(tmp_path)
    (folder / "pending-a.json").write_text(json.dumps({**PAYLOAD, "call_id": "a"}))
    (folder / "crm-b.json").write_text(json.dumps({"payload": {**PAYLOAD, "call_id": "b"}, "error": "e"}))
    (folder / "20260101_email.json").write_text("{}")  # other failure records are left alone
    failed = await replay_all(_config(tmp_path), initial_delay=0)
    assert failed == 0
    out = capsys.readouterr().out
    assert "pending-a.json: ok" in out and "crm-b.json: ok" in out
    assert [p.name for p in folder.glob("*.json")] == ["20260101_email.json"]
