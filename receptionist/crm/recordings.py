# receptionist/crm/recordings.py
"""Serve local call recordings to the CRM's recording proxy (HTTP Basic, user "crm", Range support).

Run: python -m receptionist.crm.recordings   (put it behind HTTPS on a public host)
"""
from __future__ import annotations

import hmac
import logging
import os
from pathlib import Path

from aiohttp import BasicAuth, web

logger = logging.getLogger("receptionist")
USER = "crm"


def make_app(root: Path, password: str) -> web.Application:
    if not password:
        raise ValueError("CRM_RECORDINGS_PASSWORD must be set")
    root = Path(root).resolve()

    async def handle(request: web.Request) -> web.StreamResponse:
        try:
            auth = BasicAuth.decode(request.headers.get("Authorization", ""))
        except ValueError:
            auth = None
        if auth is None or auth.login != USER or not hmac.compare_digest(auth.password.encode(), password.encode()):
            return web.Response(status=401, headers={"WWW-Authenticate": 'Basic realm="recordings"'})
        target = (root / request.match_info["path"]).resolve()
        if root not in target.parents or not target.is_file():
            return web.Response(status=404)
        return web.FileResponse(target)

    app = web.Application()
    app.router.add_get("/recordings/{path:.+}", handle)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    root = Path(os.environ.get("CRM_RECORDINGS_ROOT", "./recordings"))
    port = int(os.environ.get("CRM_RECORDINGS_PORT", "8089"))
    web.run_app(make_app(root, os.environ.get("CRM_RECORDINGS_PASSWORD", "")), port=port)


if __name__ == "__main__":
    main()
