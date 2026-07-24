"""Tests for security defaults and configuration."""

from mycellm.config.settings import MycellmSettings

# These assert the in-code defaults, so they must not read the developer's
# ~/.config/mycellm/.env (which commonly sets MYCELLM_QUIC_HOST=0.0.0.0).


def test_default_host_is_localhost():
    """Default bind should be localhost, not 0.0.0.0."""
    settings = MycellmSettings(_env_file=None)
    assert settings.api_host == "127.0.0.1"
    assert settings.quic_host == "127.0.0.1"


def test_default_api_key_empty():
    settings = MycellmSettings(_env_file=None)
    assert settings.api_key == ""


def test_default_initial_credits():
    settings = MycellmSettings(_env_file=None)
    assert settings.initial_credits == 100.0
