# receptionist/crm/modes.py
"""Plan-limit modes as config transforms, so the call loop needs no special cases. Pure."""
from __future__ import annotations

from receptionist.config import BusinessConfig

MESSAGE_ONLY_SECONDS = 45
OFF_SECONDS = 8
UNAVAILABLE_GREETING = "Sorry, this line isn't available right now. Please try again later."
_MESSAGE_ONLY_PERSONALITY = (
    "Take a message only. Ask for the caller's name, callback number and reason for calling, "
    "save it with the take_message tool, thank them and end the call. Do not answer other "
    "questions, look up information or offer transfers."
)
_OFF_PERSONALITY = "Say only the greeting, then end the call. Do not take messages or answer questions."


def _restricted(
    config: BusinessConfig, greeting: str, personality: str, seconds: int, extra: dict | None = None,
) -> BusinessConfig:
    idle = config.voice.idle.model_copy(update={"max_call_duration_seconds": seconds})
    voice = config.voice.model_copy(update={"idle": idle})
    return config.model_copy(update={
        "greeting": greeting,
        "personality": personality,
        "routing": [],
        "faqs": [],
        "calendar": None,
        "intakes": None,
        "info_packets": None,
        "dtmf": None,
        "voice": voice,
        **(extra or {}),
    })


def message_only_config(config: BusinessConfig) -> BusinessConfig:
    greeting = (
        f"Thanks for calling {config.business.name}. Please leave your name, number and the "
        "reason for your call, and we'll get back to you."
    )
    return _restricted(config, greeting, _MESSAGE_ONLY_PERSONALITY, MESSAGE_ONLY_SECONDS)


def off_config(config: BusinessConfig) -> BusinessConfig:
    # No recording in off mode: the consent preamble would eat the 8 s cap.
    return _restricted(config, UNAVAILABLE_GREETING, _OFF_PERSONALITY, OFF_SECONDS, {"recording": None})


def config_for_mode(config: BusinessConfig, mode: str) -> BusinessConfig:
    if mode == "message_only":
        return message_only_config(config)
    if mode == "off":
        return off_config(config)
    return config
