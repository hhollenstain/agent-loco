from __future__ import annotations

from agent_loco.hardware import HardwareProfile, detect_hardware


def test_detect_hardware_returns_profile() -> None:
    profile = detect_hardware()
    assert isinstance(profile, HardwareProfile)
    assert profile.os_name
    assert profile.arch
    assert profile.recommended_backend
    assert profile.recommended_model
