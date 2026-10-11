from __future__ import annotations

import pytest
from aiohttp import BasicAuth
from aiohttp.test_utils import TestClient, TestServer

from receptionist.crm.recordings import make_app


@pytest.fixture
def root(tmp_path):
    base = tmp_path / "recordings"
    (base / "acme").mkdir(parents=True)
    (base / "acme" / "a.ogg").write_bytes(b"0123456789")
    (tmp_path / "secret.txt").write_text("nope")
    return base


async def _client(root):
    client = TestClient(TestServer(make_app(root, "rec-pass")))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_requires_auth(root):
    client = await _client(root)
    try:
        assert (await client.get("/recordings/acme/a.ogg")).status == 401
        assert (await client.get("/recordings/acme/a.ogg", auth=BasicAuth("crm", "wrong"))).status == 401
        assert (await client.get("/recordings/acme/a.ogg", auth=BasicAuth("other", "rec-pass"))).status == 401
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_serves_file_with_range(root):
    client = await _client(root)
    try:
        resp = await client.get("/recordings/acme/a.ogg", auth=BasicAuth("crm", "rec-pass"))
        assert resp.status == 200 and await resp.read() == b"0123456789"
        resp = await client.get("/recordings/acme/a.ogg", auth=BasicAuth("crm", "rec-pass"), headers={"Range": "bytes=2-4"})
        assert resp.status == 206 and await resp.read() == b"234"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_no_path_traversal_or_missing(root):
    client = await _client(root)
    auth = BasicAuth("crm", "rec-pass")
    try:
        assert (await client.get("/recordings/../secret.txt", auth=auth)).status == 404
        assert (await client.get("/recordings/%2e%2e/secret.txt", auth=auth)).status == 404
        assert (await client.get("/recordings/acme/missing.ogg", auth=auth)).status == 404
        assert (await client.get("/recordings/acme", auth=auth)).status == 404
    finally:
        await client.close()


def test_empty_password_refused(root):
    with pytest.raises(ValueError):
        make_app(root, "")
