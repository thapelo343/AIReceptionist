# receptionist/crm/replay.py
"""Resend call results that never reached the CRM.

    python -m receptionist.crm.replay --config config/businesses/acme.yaml --all
    python -m receptionist.crm.replay --config config/businesses/acme.yaml <file.json>

`--all` scans `pending-*.json` (cut off mid-delivery) and `crm-*.json` (failed
delivery) in the business's failures dir. Each file is re-signed and resent;
it is deleted on success and kept (with the new error) on failure.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from receptionist.crm.delivery import deliver_call_result, failures_dir, safe_call_id


def _payload_of(data: Any) -> dict | None:
    if isinstance(data, dict) and "v" not in data and isinstance(data.get("payload"), dict):
        data = data["payload"]  # crm-*.json archive record
    if isinstance(data, dict) and "v" in data and data.get("call_id"):
        return data
    return None


async def replay_file(config: Any, path: Path, *, initial_delay: float = 1.0) -> tuple[bool, str]:
    """Resend one pending/archived file. Returns (ok, "ok" or the failure reason)."""
    try:
        payload = _payload_of(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError) as e:
        return False, f"unreadable: {e}"
    if payload is None:
        return False, "not a complete CallResult (the call result could not be built; see its error)"
    if await deliver_call_result(config, payload, initial_delay=initial_delay):
        Path(path).unlink(missing_ok=True)
        return True, "ok"
    archived = failures_dir(config) / f"crm-{safe_call_id(payload['call_id'])}.json"
    try:
        error = json.loads(archived.read_text(encoding="utf-8")).get("error", "unknown error")
    except (OSError, ValueError):
        error = "unknown error"
    return False, f"{error} (kept as {archived.name})"


async def replay_all(config: Any, *, initial_delay: float = 1.0) -> int:
    """Resend every pending/archived call result; print one line per file. Returns the failure count."""
    folder = failures_dir(config)
    files = sorted(folder.glob("pending-*.json")) + sorted(folder.glob("crm-*.json")) if folder.is_dir() else []
    failed = 0
    for path in files:
        if not path.exists():  # a pending file replayed earlier in this run may have been archived
            continue
        ok, reason = await replay_file(config, path, initial_delay=initial_delay)
        print(f"{path.name}: ok" if ok else f"{path.name}: failed: {reason}")
        failed += 0 if ok else 1
    if not files:
        print(f"nothing to replay in {folder}")
    return failed


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    from receptionist.config import load_config

    parser = argparse.ArgumentParser(prog="python -m receptionist.crm.replay", description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True, help="business YAML (for the CRM url, secret env and failures dir)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="resend every pending-*.json and crm-*.json")
    group.add_argument("file", nargs="?", help="one pending-*.json or crm-*.json file")
    args = parser.parse_args(argv)

    load_dotenv(".env.local")
    load_dotenv(".env")
    config = load_config(args.config)
    if config.crm is None:
        print(f"{args.config} has no crm: block", file=sys.stderr)
        return 2
    if args.all:
        return 1 if asyncio.run(replay_all(config)) else 0
    ok, reason = asyncio.run(replay_file(config, Path(args.file)))
    print(f"{Path(args.file).name}: ok" if ok else f"{Path(args.file).name}: failed: {reason}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
