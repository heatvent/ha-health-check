"""Button that runs the house-health checklist now."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the run button."""
    async_add_entities([HouseHealthButton(hass.data[DOMAIN][entry.entry_id])])


class HouseHealthButton(ButtonEntity):
    """Generate the checklist immediately."""

    _attr_has_entity_name = True
    _attr_name = "Run report"
    _attr_icon = "mdi:clipboard-check"

    def __init__(self, runtime) -> None:
        self._runtime = runtime
        self._attr_unique_id = f"{runtime.entry.entry_id}_report"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.entry.entry_id)},
            name="HA Health Check",
            manufacturer="Home",
            model="Weekly checklist",
        )

    async def async_press(self) -> None:
        """Build the checklist, notify, and email it."""
        await self._runtime.async_report()
