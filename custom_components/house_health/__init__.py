"""Weekly house-health checklist."""

from __future__ import annotations

import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    EMAIL_ENTITY,
    NOTIFICATION_ID,
    REPORT_HOUR,
    REPORT_MINUTE,
    REPORT_WEEKDAY,
    SIGNAL_REPORT,
)
from .report import Report, async_build_report

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor", "button"]


class HouseHealthRuntime:
    """One checklist, scheduled and runnable by hand."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.last_report: Report | None = None
        self._running = False

    async def async_report(self) -> None:
        """Build the checklist, save a notification, and email Matt."""
        if self._running:
            _LOGGER.info("House health report is already running")
            return
        self._running = True
        try:
            report = await async_build_report(self.hass, self.entry.options)
            self.last_report = report
            async_dispatcher_send(self.hass, SIGNAL_REPORT)
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": report.title,
                    "message": report.body,
                    "notification_id": NOTIFICATION_ID,
                },
                blocking=True,
            )
            if self.hass.states.get(EMAIL_ENTITY) is None:
                _LOGGER.error("House health could not email %s because it does not exist", EMAIL_ENTITY)
                return
            await self.hass.services.async_call(
                "notify",
                "send_message",
                {"message": report.body, "title": report.title},
                target={"entity_id": EMAIL_ENTITY},
                blocking=True,
            )
            _LOGGER.info("House health report sent: %s", report.title)
        finally:
            self._running = False


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up House Health."""
    if entry.title != "HA Health Check":
        hass.config_entries.async_update_entry(entry, title="HA Health Check")
    runtime = HouseHealthRuntime(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _weekly(now) -> None:
        if dt_util.as_local(now).weekday() != REPORT_WEEKDAY:
            return
        await runtime.async_report()

    async def _run_service(_call: ServiceCall) -> None:
        await runtime.async_report()

    entry.async_on_unload(async_track_time_change(hass, _weekly, hour=REPORT_HOUR, minute=REPORT_MINUTE, second=20))
    hass.services.async_register(DOMAIN, "report", _run_service)
    entry.async_on_unload(lambda: hass.services.async_remove(DOMAIN, "report"))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload House Health."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
