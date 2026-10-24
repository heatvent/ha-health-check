"""Sensor that remembers the last house-health checklist."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SIGNAL_REPORT
from .report import report_from_restored_state


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the last-report sensor."""
    async_add_entities([HouseHealthSensor(hass.data[DOMAIN][entry.entry_id])])


class HouseHealthSensor(RestoreEntity, SensorEntity):
    """Timestamp of the last checklist. The text is in the attributes."""

    _attr_has_entity_name = True
    _attr_name = "Last report"
    _attr_icon = "mdi:clipboard-text"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _unrecorded_attributes = frozenset({"report"})

    def __init__(self, runtime) -> None:
        self._runtime = runtime
        self._attr_unique_id = f"{runtime.entry.entry_id}_last_report"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.entry.entry_id)},
            name="HA Health Check",
            manufacturer="Home",
            model="Weekly checklist",
        )

    async def async_added_to_hass(self) -> None:
        """Refresh when a report is published, and restore the previous one."""
        await super().async_added_to_hass()
        if self._runtime.last_report is None:
            last = await self.async_get_last_state()
            if last is not None and last.state not in {STATE_UNKNOWN, STATE_UNAVAILABLE}:
                restored = report_from_restored_state(last)
                if restored is not None:
                    self._runtime.consider_report(restored)
        self._apply()
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_REPORT, self._handle))

    @callback
    def _handle(self) -> None:
        self._apply()
        self.async_write_ha_state()

    def _apply(self) -> None:
        report = self._runtime.last_report
        if report is None:
            self._attr_native_value = None
            return
        self._attr_native_value = dt_util.as_local(report.created)
        self._attr_extra_state_attributes = {
            "title": report.title,
            "fix": report.fix_count,
            "check": report.check_count,
            "report": report.body,
        }
