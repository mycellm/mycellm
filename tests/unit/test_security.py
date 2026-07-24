"""Tests for security defaults and configuration."""

import os

import pytest

from mycellm.config.settings import MycellmSettings


@pytest.fixture
def defaults(monkeypatch):
    """Settings built from code defaults only.

    These tests assert what mycellm *ships* as its defaults, so the operator
    config of whatever machine runs the suite must not leak in: drop MYCELLM_*
    from the environment and skip the .env files (cwd and ~/.config/mycellm).
    """
    for key in list(os.environ):
        if key.startswith("MYCELLM_"):
            monkeypatch.delenv(key, raising=False)
    return MycellmSettings(_env_file=None)


def test_default_host_is_localhost(defaults):
    """Default bind should be localhost, not 0.0.0.0."""
    assert defaults.api_host == "127.0.0.1"
    assert defaults.quic_host == "127.0.0.1"


def test_default_api_key_empty(defaults):
    assert defaults.api_key == ""


def test_default_initial_credits(defaults):
    assert defaults.initial_credits == 100.0
