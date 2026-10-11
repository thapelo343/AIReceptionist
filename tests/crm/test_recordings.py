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


import asyncio
import base64
import os

_CRED = base64.b64encode(b"crm:rec-pass").decode()


async def _raw(server, path, auth=f"Basic {_CRED}"):
    reader, writer = await asyncio.open_connection(server.host, server.port)
    head = f"GET {path} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n"
    if auth is not None:
        head += f"Authorization: {auth}\r\n"
    writer.write((head + "\r\n").encode())
    await writer.drain()
    data = await reader.read()
    writer.close()
    return data.split(b"\r\n", 1)[0].decode(), data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/recordings/..%2fsecret.txt",
        "/recordings/acme%2f..%2f..%2fsecret.txt",
        "/recordings/..%5csecret.txt",
        "/recordings/%2fetc/passwd",
        "/recordings//etc/passwd",
        "/recordings/C:/Windows/win.ini",
    ],
)
async def test_guard_blocks_escapes_at_handler(root, path):
    server = TestServer(make_app(root, "rec-pass"))
    await server.start_server()
    try:
        status, data = await _raw(server, path)
        assert " 404 " in status, status
        assert b"nope" not in data
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_guard_blocks_symlink_outside_root(root):
    try:
        os.symlink(root.parent / "secret.txt", root / "acme" / "link.ogg")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    server = TestServer(make_app(root, "rec-pass"))
    await server.start_server()
    try:
        status, data = await _raw(server, "/recordings/acme/link.ogg")
        assert " 404 " in status, status
        assert b"nope" not in data
    finally:
        await server.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "auth",
    [None, "", "Basic", "Basic ", "Basic !!!notbase64", "Basic " + base64.b64encode(b"nocolon").decode(), "Bearer x"],
)
async def test_malformed_auth_is_401(root, auth):
    server = TestServer(make_app(root, "rec-pass"))
    await server.start_server()
    try:
        status, data = await _raw(server, "/recordings/acme/a.ogg", auth=auth)
        assert " 401 " in status, status
        assert b"www-authenticate" in data.lower()
    finally:
        await server.close()
