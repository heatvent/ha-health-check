"""Shared fixtures: load this repo's custom_components in the HA test harness."""

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant find custom_components/ during tests."""
    yield
