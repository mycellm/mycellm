"""launchd plist rendering for `mycellm serve --install-service` (macOS).

Renders the template only — nothing here touches launchctl or writes into
~/Library/LaunchAgents, so it runs on any platform.
"""

import plistlib

from mycellm.cli.serve import (
    LAUNCHD_HARD_FILE_LIMIT,
    LAUNCHD_SOFT_FILE_LIMIT,
    _render_launchd_plist,
)


def render(**overrides):
    kwargs = {
        "mycellm_bin": "/usr/local/bin/mycellm",
        "host": "127.0.0.1",
        "port": 8420,
        "quic_port": 8421,
        "dht_port": 8422,
        "device": "default",
        "no_dht": False,
        "priority": "normal",
    }
    kwargs.update(overrides)
    return plistlib.loads(_render_launchd_plist(**kwargs).encode())


def test_plist_parses_and_keeps_existing_keys():
    parsed = render()

    assert parsed["Label"] == "com.mycellm.node"
    assert parsed["RunAtLoad"] is True
    assert parsed["KeepAlive"] == {"SuccessfulExit": False}
    assert parsed["ThrottleInterval"] == 10
    assert parsed["Nice"] == 0
    assert parsed["ProcessType"] == "Standard"
    assert parsed["ProgramArguments"][:2] == ["/usr/local/bin/mycellm", "serve"]


def test_plist_sets_number_of_files_limits():
    parsed = render()

    assert parsed["SoftResourceLimits"]["NumberOfFiles"] == LAUNCHD_SOFT_FILE_LIMIT
    assert parsed["HardResourceLimits"]["NumberOfFiles"] == LAUNCHD_HARD_FILE_LIMIT


def test_soft_limit_is_65536_and_hard_is_not_lower():
    # launchd refuses a job whose hard limit sits below its soft limit.
    assert LAUNCHD_SOFT_FILE_LIMIT == 65536
    assert LAUNCHD_HARD_FILE_LIMIT >= LAUNCHD_SOFT_FILE_LIMIT


def test_limits_survive_the_other_template_branches(monkeypatch):
    monkeypatch.setenv("MYCELLM_BOOTSTRAP_PEERS", "bootstrap.mycellm.dev:8421")
    parsed = render(no_dht=True, priority="low")

    assert parsed["EnvironmentVariables"] == {
        "MYCELLM_BOOTSTRAP_PEERS": "bootstrap.mycellm.dev:8421"
    }
    assert "--no-dht" in parsed["ProgramArguments"]
    assert parsed["ProcessType"] == "Background"
    assert parsed["SoftResourceLimits"]["NumberOfFiles"] == LAUNCHD_SOFT_FILE_LIMIT
    assert parsed["HardResourceLimits"]["NumberOfFiles"] == LAUNCHD_HARD_FILE_LIMIT
