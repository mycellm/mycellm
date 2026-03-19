"""Test the E2E harness itself — spawn, check, kill."""

import pytest

from tests.e2e.harness import E2EHarness


def test_harness_setup():
    """Test that harness creates node configs correctly."""
    harness = E2EHarness(base_port=19420, node_count=3)
    try:
        nodes = harness.setup()
        assert len(nodes) == 3

        # Check port assignment
        assert nodes[0].api_port == 19420
        assert nodes[1].api_port == 19430
        assert nodes[2].api_port == 19440

        # Check data dirs exist
        for node in nodes:
            assert node.data_dir.exists()
    finally:
        harness.teardown()


def test_harness_provision_identity():
    """Test that identity provisioning creates keys and certs."""
    harness = E2EHarness(base_port=19520, node_count=1)
    try:
        nodes = harness.setup()
        harness.provision_identity(nodes[0])

        keys_dir = nodes[0].data_dir / "keys"
        certs_dir = nodes[0].data_dir / "certs"

        assert (keys_dir / "account.key").exists()
        assert (keys_dir / "device-default.key").exists()
        assert (certs_dir / "device-default.cert").exists()
    finally:
        harness.teardown()
