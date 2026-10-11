# receptionist/crm/result.py
"""Build and sign the CallResult v1 payload. Pure: no network, no LiveKit."""
from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import quote
from zoneinfo import ZoneInfo

from receptionist.messaging.models import Message
from receptionist.transcript.capture import SpeakerRole, TranscriptSegment
from receptionist.transcript.metadata import CallMetadata

logger = logging.getLogger("receptionist")
VERSION = 1
_ROLES = {SpeakerRole.ASSISTANT: "agent", SpeakerRole.USER: "caller"}


def pick_outcome(outcomes: set[str], after_hours: bool) -> str:
    """Collapse the fork's multi-outcome set into one CRM outcome: transfer > message > after_hours > hangup."""
    if "transferred" in outcomes:
        return "transfer"
    if "message_taken" in outcomes:
        return "message"
    return "after_hours" if after_hours else "hangup"


def is_after_hours(hours: Any, tz_name: str, started_at: datetime) -> bool:
    local = started_at.astimezone(ZoneInfo(tz_name))
    day = getattr(hours, local.strftime("%A").lower(), None)
    if day is None:
        return True
    now = local.strftime("%H:%M")
    return not (day.open <= now <= day.close)


def recording_url(artifact_url: str | None, root: str, base_url: str | None) -> str | None:
    """Public URL of a local recording under `root`, served by receptionist.crm.recordings. None for S3/outside root."""
    if not artifact_url or not base_url or "://" in artifact_url:
        return None
    try:
        relative = Path(artifact_url).resolve().relative_to(Path(root).resolve())
    except ValueError:
        return None
    return f"{base_url.rstrip('/')}/{quote(relative.as_posix())}"


def caller_number(phone: str | None) -> str:
    """The caller's number as the CRM expects it. Anonymous stays "".

    A national number from SIP ("0821234567") gets a bogus "+" from
    _normalize_sip_phone ("+0821234567"); send it back as national so the CRM
    normalizes it to E.164 using the business number's country.
    """
    if not phone:
        return ""
    return phone[1:] if phone.startswith("+0") else phone


def _recording_drop_reason(artifact_url: str, root: str, base_url: str | None) -> str:
    if not base_url:
        return "crm.recordings_base_url is not set"
    if "://" in artifact_url:
        return "the recording is not a local file (local storage is required)"
    return f"the recording is outside crm.recordings_root ({root})"


def build_call_result(
    config: Any,
    metadata: CallMetadata,
    segments: Sequence[TranscriptSegment],
    messages: Sequence[Message],
    summary: str | None,
    artifact_url: str | None,
    *,
    limited: bool = False,
) -> dict:
    crm = config.crm
    started = datetime.fromisoformat(metadata.start_ts)
    last = messages[-1] if messages else None
    message: dict[str, str] = {}
    if last is not None:
        message = {k: v for k, v in {"reason": last.message, "callback_number": last.callback_number}.items() if v}
    rec_url = recording_url(artifact_url, crm.recordings_root, crm.recordings_base_url)
    recording = getattr(config, "recording", None)
    if rec_url is None and artifact_url and recording is not None and recording.enabled:
        logger.warning(
            "CRM call result %s sent without recording: %s", metadata.call_id,
            _recording_drop_reason(artifact_url, crm.recordings_root, crm.recordings_base_url),
            extra={"call_id": metadata.call_id, "component": "crm.result"},
        )
    return {
        "v": VERSION,
        "call_id": metadata.call_id,
        "from": caller_number(metadata.caller_phone),
        "to": crm.business_number,
        "started_at": metadata.start_ts,
        "ended_at": metadata.end_ts,
        "duration_seconds": int(round(metadata.duration_seconds)) if metadata.duration_seconds is not None else None,
        "outcome": pick_outcome(set(metadata.outcomes), is_after_hours(config.hours, config.business.timezone, started)),
        "transfer_to": metadata.transfer_target,
        "caller_name": (last.caller_name or None) if last is not None else None,
        "summary": summary or "",
        "message": message,
        "language": sorted(metadata.languages_detected)[0] if metadata.languages_detected else None,
        "transcript": [
            {"role": _ROLES[s.role], "text": s.text}
            for s in segments
            if s.role in _ROLES and s.text
        ],
        "recording_url": rec_url,
        "limited": bool(limited),
    }


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
