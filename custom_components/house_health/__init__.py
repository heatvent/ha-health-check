"""Weekly house-health checklist."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    NOTIFICATION_ID,
    REPORT_CATCHUP_DAYS,
    REPORT_HOUR,
    REPORT_MINUTE,
    REPORT_WEEKDAY,
    SIGNAL_REPORT,
)
from .report import (
    Report,
    async_build_report,
    notify_entity,
    report_from_storage,
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor", "button"]
STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.last_report"

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def _same_local_day(report: Report | None, moment: datetime) -> bool:
    """True when a checklist was already built on this local date."""
    if report is None:
        return False
    return dt_util.as_local(report.created).date() == dt_util.as_local(moment).date()


class HouseHealthRuntime:
    """One checklist, scheduled and runnable by hand."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.last_report: Report | None = None
        self._running = False
        self._store: Store[dict] = Store(hass, STORAGE_VERSION, STORAGE_KEY)

    def consider_report(self, report: Report) -> None:
        """Keep the newer of two saved checklists."""
        if self.last_report is None or report.created > self.last_report.created:
            self.last_report = report

    async def async_report(self) -> None:
        """Build the checklist, save a notification, and email it."""
        if self._running:
            _LOGGER.info("House health report is already running")
            return
        self._running = True
        try:
            report = await async_build_report(self.hass, self.entry.options)
            self.last_report = report
            async_dispatcher_send(self.hass, SIGNAL_REPORT)
            try:
                await self._store.async_save(
                    {
                        "title": report.title,
                        "body": report.body,
                        "fix": report.fix_count,
                        "check": report.check_count,
                        "created": report.created.isoformat(),
                    }
                )
            except Exception:
                _LOGGER.exception("House health could not save the last report")
            try:
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
            except Exception:
                _LOGGER.exception("House health could not save the notification")
            target = notify_entity(self.entry.options)
            if self.hass.states.get(target) is None:
                _LOGGER.error("House health could not email %s because it does not exist", target)
                return
            await self.hass.services.async_call(
                "notify",
                "send_message",
                {"message": report.body, "title": report.title},
                target={"entity_id": target},
                blocking=True,
            )
            _LOGGER.info("House health report sent: %s", report.title)
        finally:
            self._running = False

    async def _restore(self) -> None:
        """Load the newest saved checklist from disk."""
        try:
            stored = await self._store.async_load()
        except Exception:
            _LOGGER.exception("House health could not load the saved report")
            stored = None
        if isinstance(stored, dict):
            restored = report_from_storage(stored)
            if restored is not None:
                self.consider_report(restored)
        if self.last_report is not None:
            async_dispatcher_send(self.hass, SIGNAL_REPORT)

    async def async_startup(self, _event=None) -> None:
        """Restore the last checklist and send one if a Sunday was missed."""
        try:
            await self._restore()
            report = self.last_report
            if report is None:
                return
            age = dt_util.utcnow() - report.created
            if age < timedelta(days=REPORT_CATCHUP_DAYS):
                return
            if _same_local_day(report, dt_util.utcnow()):
                return
            await self.async_report()
        except Exception:
            _LOGGER.exception("House health startup check failed")


type HouseHealthConfigEntry = ConfigEntry[HouseHealthRuntime]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the report action once, so automations validate before the entry loads."""

    async def _run_service(_call: ServiceCall) -> None:
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.state is ConfigEntryState.LOADED:
                await entry.runtime_data.async_report()
                return
        raise HomeAssistantError("HA Health Check is not loaded")

    hass.services.async_register(DOMAIN, "report", _run_service)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HouseHealthConfigEntry) -> bool:
    """Set up House Health."""
    if entry.title != "HA Health Check":
        hass.config_entries.async_update_entry(entry, title="HA Health Check")
    runtime = HouseHealthRuntime(hass, entry)
    entry.runtime_data = runtime
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _weekly(now) -> None:
        if dt_util.as_local(now).weekday() != REPORT_WEEKDAY:
            return
        if _same_local_day(runtime.last_report, now):
            return
        await runtime.async_report()

    async def _startup(_hass: HomeAssistant | None = None) -> None:
        await runtime.async_startup()

    entry.async_on_unload(
        async_track_time_change(hass, _weekly, hour=REPORT_HOUR, minute=REPORT_MINUTE, second=20)
    )
    if hass.is_running:
        entry.async_on_unload(hass.async_create_task(_startup()).cancel)
    else:
        # async_at_started's cancel is safe after the event fired; a bare listen_once
        # unsubscribe is not and logs an error when the entry later unloads.
        entry.async_on_unload(async_at_started(hass, _startup))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HouseHealthConfigEntry) -> bool:
    """Unload House Health."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
