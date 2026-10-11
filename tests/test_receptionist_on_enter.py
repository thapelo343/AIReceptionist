# tests/test_receptionist_on_enter.py
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from receptionist.agent import Receptionist
from receptionist.config import (
    BusinessConfig, ConsentPreambleConfig, LocalStorageConfig,
    RecordingConfig, RecordingStorageConfig,
)
from receptionist.lifecycle import CallLifecycle


def _with_recording(config: BusinessConfig, tmp_path, preamble_enabled: bool) -> BusinessConfig:
    return config.model_copy(update={
        "recording": RecordingConfig(
            enabled=True,
            storage=RecordingStorageConfig(
                type="local", local=LocalStorageConfig(path=str(tmp_path)),
            ),
            consent_preamble=ConsentPreambleConfig(
                enabled=preamble_enabled,
                text="This call may be recorded for quality purposes.",
            ),
        ),
    })


@pytest.fixture
def patched_session(monkeypatch):
    """Patch Agent.session (read-only property) to return a mock at the class level.

    Agent.session is `@property` with no setter; we cannot set it per-instance.
    Replacing it with a new property at the class level gives every instance
    the same mock_session for the duration of the test.
    """
    mock_session = MagicMock()
    mock_session.generate_reply = AsyncMock()
    from livekit.agents import Agent
    monkeypatch.setattr(Agent, "session", property(lambda self: mock_session))
    return mock_session


@pytest.mark.asyncio
async def test_on_enter_speaks_preamble_before_greeting(v2_yaml, tmp_path, patched_session):
    config = _with_recording(BusinessConfig.from_yaml_string(v2_yaml), tmp_path, True)
    lifecycle = CallLifecycle(config=config, call_id="room-1", caller_phone=None)

    calls: list[str] = []

    async def _record(**kwargs) -> None:
        calls.append(kwargs.get("instructions", ""))

    patched_session.generate_reply = AsyncMock(side_effect=_record)

    receptionist = Receptionist(config, lifecycle)
    await receptionist.on_enter()

    # Preamble must be the FIRST instruction
    assert len(calls) == 2
    assert "recorded for quality purposes" in calls[0]
    assert config.greeting in calls[1]


@pytest.mark.asyncio
async def test_on_enter_skips_preamble_when_recording_disabled(v2_yaml, tmp_path, patched_session):
    # Recording not configured at all
    config = BusinessConfig.from_yaml_string(v2_yaml)
    lifecycle = CallLifecycle(config=config, call_id="room-1", caller_phone=None)

    receptionist = Receptionist(config, lifecycle)
    await receptionist.on_enter()

    # Only greeting, no preamble
    assert patched_session.generate_reply.call_count == 1
    kwargs = patched_session.generate_reply.call_args.kwargs
    assert config.greeting in kwargs["instructions"]


@pytest.mark.asyncio
async def test_on_enter_skips_preamble_when_preamble_disabled(v2_yaml, tmp_path, patched_session):
    """Recording enabled but consent_preamble.enabled=False → no preamble spoken."""
    config = _with_recording(BusinessConfig.from_yaml_string(v2_yaml), tmp_path, False)
    lifecycle = CallLifecycle(config=config, call_id="room-1", caller_phone=None)

    receptionist = Receptionist(config, lifecycle)
    await receptionist.on_enter()

    assert patched_session.generate_reply.call_count == 1


@pytest.mark.asyncio
async def test_on_enter_off_mode_greets_then_ends_without_goodbye(v2_yaml, patched_session, monkeypatch):
    """Off mode: speak the unavailable greeting, wait for it, then hang up with no second goodbye."""
    from receptionist.crm.modes import off_config

    config = off_config(BusinessConfig.from_yaml_string(v2_yaml))
    lifecycle = CallLifecycle(config=config, call_id="room-1", caller_phone=None, crm_mode="off")
    events: list[str] = []
    handle = MagicMock()
    handle.wait_for_playout = AsyncMock(side_effect=lambda: events.append("playout"))
    patched_session.generate_reply = MagicMock(return_value=handle)
    job_ctx = MagicMock()
    monkeypatch.setattr("receptionist.agent.get_job_context", lambda: job_ctx)
    terminate = AsyncMock(side_effect=lambda *a, **k: events.append("terminate"))
    monkeypatch.setattr("receptionist.agent._speak_goodbye_and_terminate", terminate)

    receptionist = Receptionist(config, lifecycle)
    await receptionist.on_enter()
    import asyncio
    await asyncio.sleep(0)  # let the background hangup task run

    assert patched_session.generate_reply.call_count == 1
    assert config.greeting in patched_session.generate_reply.call_args.kwargs["instructions"]
    assert events == ["playout", "terminate"]
    assert lifecycle.metadata.agent_end_reason == "plan_off"
    args, kwargs = terminate.call_args
    assert args[0] is None, "no session: nothing more is spoken"
    assert args[1] is lifecycle and args[2] is job_ctx
    assert kwargs["reason"] == "plan_off"


@pytest.mark.asyncio
async def test_on_enter_normal_mode_does_not_hang_up(v2_yaml, patched_session, monkeypatch):
    config = BusinessConfig.from_yaml_string(v2_yaml)
    lifecycle = CallLifecycle(config=config, call_id="room-1", caller_phone=None)
    terminate = AsyncMock()
    monkeypatch.setattr("receptionist.agent._speak_goodbye_and_terminate", terminate)

    await Receptionist(config, lifecycle).on_enter()

    terminate.assert_not_awaited()
    assert lifecycle.metadata.agent_end_reason is None
