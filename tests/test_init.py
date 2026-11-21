"""Entry setup, the report action, and unload."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.house_health.const import DOMAIN


async def test_setup_and_unload(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="HA Health Check", data={})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not None
    assert hass.services.has_service(DOMAIN, "report")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_report_action_runs_the_loaded_entry(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="HA Health Check", data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    with patch.object(entry.runtime_data, "async_report", AsyncMock()) as report:
        await hass.services.async_call(DOMAIN, "report", {}, blocking=True)
    report.assert_awaited_once()


async def test_report_action_exists_without_an_entry(hass: HomeAssistant) -> None:
    """Registered in async_setup, so automations validate; it errors clearly when unused."""
    assert await async_setup_component(hass, DOMAIN, {})
    assert hass.services.has_service(DOMAIN, "report")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "report", {}, blocking=True)


async def test_critical_pattern_row_checks_each_match(hass: HomeAssistant) -> None:
    from custom_components.house_health.report import critical_states

    hass.states.async_set("binary_sensor.life360_online_a_example_com", "on")
    hass.states.async_set("binary_sensor.life360_online_b_example_com", "off")
    found = critical_states(hass, "binary_sensor.life360_online_*")
    assert [entity_id for entity_id, _ in found] == [
        "binary_sensor.life360_online_a_example_com",
        "binary_sensor.life360_online_b_example_com",
    ]
    assert critical_states(hass, "binary_sensor.nothing_*") == [("binary_sensor.nothing_*", None)]


async def test_report_without_recipient_skips_email(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="HA Health Check", data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    sent = async_mock_service(hass, "notify", "send_message")
    await entry.runtime_data.async_report()
    assert sent == []
    assert entry.runtime_data.last_report is not None
