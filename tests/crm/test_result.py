from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from receptionist.crm.result import (
    build_call_result, is_after_hours, pick_outcome, recording_url, sign,
)
from receptionist.messaging.models import Message
from receptionist.transcript.capture import SpeakerRole, TranscriptSegment
from receptionist.transcript.metadata import CallMetadata


def _hours(**days):
    names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    return SimpleNamespace(**{d: days.get(d) for d in names})


OPEN = SimpleNamespace(open="08:00", close="17:00")


def test_pick_outcome_priority():
    assert pick_outcome({"transferred", "message_taken"}, after_hours=True) == "transfer"
    assert pick_outcome({"message_taken", "agent_ended"}, after_hours=True) == "message"
    assert pick_outcome({"hung_up"}, after_hours=True) == "after_hours"
    assert pick_outcome({"hung_up"}, after_hours=False) == "hangup"
    assert pick_outcome(set(), after_hours=False) == "hangup"


def test_is_after_hours():
    hours = _hours(saturday=None, friday=OPEN)
    # 2026-10-09 is a Friday; 07:00 UTC = 09:00 Johannesburg
    assert is_after_hours(hours, "Africa/Johannesburg", datetime(2026, 10, 9, 7, 0, tzinfo=timezone.utc)) is False
    assert is_after_hours(hours, "Africa/Johannesburg", datetime(2026, 10, 9, 16, 0, tzinfo=timezone.utc)) is True
    assert is_after_hours(hours, "Africa/Johannesburg", datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc)) is True


def test_recording_url(tmp_path):
    root = tmp_path / "recordings"
    (root / "acme").mkdir(parents=True)
    file = root / "acme" / "call 1.ogg"
    file.write_bytes(b"x")
    assert recording_url(str(file), str(root), "https://voice.example.com/recordings") == "https://voice.example.com/recordings/acme/call%201.ogg"
    assert recording_url(str(file), str(root), None) is None
    assert recording_url(None, str(root), "https://v/r") is None
    assert recording_url("s3://bucket/a.ogg", str(root), "https://v/r") is None
    assert recording_url(str(tmp_path / "outside.ogg"), str(root), "https://v/r") is None


def _config(**crm):
    return SimpleNamespace(
        business=SimpleNamespace(name="Acme Dental", timezone="Africa/Johannesburg"),
        hours=_hours(friday=OPEN),
        crm=SimpleNamespace(business_number="+27105550100", recordings_root="./recordings", recordings_base_url=None, **crm),
    )


def test_build_call_result_message_call():
    meta = CallMetadata(call_id="lk-1", business_name="Acme Dental", caller_phone="+27821234567",
                        start_ts="2026-10-09T07:00:00+00:00")
    meta.outcomes.add("message_taken")
    meta.end_ts = "2026-10-09T07:03:38+00:00"
    meta.duration_seconds = 218.4
    meta.languages_detected.add("en")
    segments = [
        TranscriptSegment(role=SpeakerRole.ASSISTANT, text="Hello", created_at=1.0),
        TranscriptSegment(role=SpeakerRole.TOOL, text="take_message", created_at=2.0),
        TranscriptSegment(role=SpeakerRole.USER, text="I need a quote", created_at=3.0),
    ]
    msg = Message(caller_name="Sipho", callback_number="+27821234567", message="Quote for 3 rooms", business_name="Acme Dental")
    result = build_call_result(_config(), meta, segments, [msg], "Wants a quote.", None)
    assert result == {
        "v": 1, "call_id": "lk-1", "from": "+27821234567", "to": "+27105550100",
        "started_at": "2026-10-09T07:00:00+00:00", "ended_at": "2026-10-09T07:03:38+00:00",
        "duration_seconds": 218, "outcome": "message", "transfer_to": None, "caller_name": "Sipho",
        "summary": "Wants a quote.",
        "message": {"reason": "Quote for 3 rooms", "callback_number": "+27821234567"},
        "language": "en",
        "transcript": [{"role": "agent", "text": "Hello"}, {"role": "caller", "text": "I need a quote"}],
        "recording_url": None,
    }


def test_build_call_result_transfer_and_anonymous():
    meta = CallMetadata(call_id="lk-2", business_name="Acme Dental", caller_phone=None,
                        start_ts="2026-10-09T07:00:00+00:00")
    meta.outcomes.add("transferred")
    meta.transfer_target = "Front desk"
    result = build_call_result(_config(), meta, [], [], None, None)
    assert (result["from"], result["outcome"], result["transfer_to"], result["summary"]) == ("", "transfer", "Front desk", "")
    assert result["message"] == {} and result["caller_name"] is None


def test_sign_matches_crm_check():
    body = json.dumps({"v": 1}).encode()
    expected = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert sign(body, "s3cret") == expected
