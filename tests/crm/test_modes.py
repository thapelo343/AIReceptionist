from __future__ import annotations

import pytest

from receptionist.config import BusinessConfig, CrmConfig
from receptionist.crm.modes import (
    MESSAGE_ONLY_SECONDS, OFF_SECONDS, UNAVAILABLE_GREETING,
    config_for_mode, message_only_config, off_config,
)


@pytest.fixture
def business(minimal_business_dict):
    cfg = BusinessConfig(**minimal_business_dict)
    return cfg.model_copy(update={"crm": CrmConfig(url="https://c.example.com/api/method/crm.channels.webhooks.voice", secret_env="X", business_number="+27105550100")})


def test_message_only_strips_tools(business):
    cfg = message_only_config(business)
    assert cfg.greeting == f"Thanks for calling {business.business.name}. Please leave your name, number and the reason for your call, and we'll get back to you."
    assert (cfg.routing, cfg.faqs, cfg.calendar, cfg.intakes, cfg.info_packets, cfg.dtmf) == ([], [], None, None, None, None)
    assert cfg.voice.idle.max_call_duration_seconds == MESSAGE_ONLY_SECONDS == 45
    assert cfg.crm == business.crm and cfg.business == business.business
    assert business.routing, "original config must be untouched"


def test_off_config(business):
    cfg = off_config(business)
    assert cfg.greeting == UNAVAILABLE_GREETING == "Sorry, this line isn't available right now. Please try again later."
    assert cfg.voice.idle.max_call_duration_seconds == OFF_SECONDS == 8
    assert cfg.routing == []


def test_config_for_mode(business):
    assert config_for_mode(business, "normal") is business
    assert config_for_mode(business, "message_only").voice.idle.max_call_duration_seconds == 45
    assert config_for_mode(business, "off").greeting == UNAVAILABLE_GREETING
    assert config_for_mode(business, "unexpected") is business


def test_off_drops_recording_message_only_keeps_it(business):
    from receptionist.config import RecordingConfig
    recording = RecordingConfig(
        enabled=True,
        storage={"type": "local", "local": {"path": "./recordings"}},
        consent_preamble={"enabled": True, "text": "This call may be recorded."},
    )
    cfg = business.model_copy(update={"recording": recording})
    assert off_config(cfg).recording is None
    assert message_only_config(cfg).recording == recording
