"""Shared fixtures for the whole test suite.

Tests assert mycellm's in-code defaults, so they must not pick up the host's
live node config. `MycellmSettings` reads a `.env` from the cwd and from the XDG
config dir (`~/.config/mycellm/.env`, which commonly sets things like
`MYCELLM_QUIC_HOST=0.0.0.0`), and any ambient `MYCELLM_*` variable overrides a
default too. Both sources are scrubbed for every test so the suite behaves the
same on a contributor's node as it does in CI.
"""

import os

import pytest

from mycellm.config import get_settings
from mycellm.config.settings import MycellmSettings


@pytest.fixture(autouse=True)
def hermetic_settings(monkeypatch):
    """Isolate each test from the host's live mycellm configuration."""
    for name in [k for k in os.environ if k.startswith("MYCELLM_")]:
        monkeypatch.delenv(name, raising=False)

    # `env_file` is resolved from the XDG config dir when the class is defined,
    # so it has to be cleared on model_config rather than via an env var.
    monkeypatch.setitem(MycellmSettings.model_config, "env_file", None)

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
