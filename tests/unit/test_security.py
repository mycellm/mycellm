"""Tests for security defaults and configuration."""

import pytest
from mycellm.config.settings import MycellmSettings


def test_default_host_is_localhost():
    """Default bind should be localhost, not 0.0.0.0."""
    settings = MycellmSettings()
    assert settings.api_host == "127.0.0.1"
    assert settings.quic_host == "127.0.0.1"


def test_default_api_key_empty():
    settings = MycellmSettings()
    assert settings.api_key == ""


def test_default_initial_credits():
    settings = MycellmSettings()
    assert settings.initial_credits == 100.0
