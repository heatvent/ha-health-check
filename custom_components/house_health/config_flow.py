"""Config flow for House Health."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import selector

from .const import (
    CRITICAL,
    DEFAULT_IGNORED_DEVICE_NAMES,
    DEFAULT_IGNORED_PLATFORMS,
    DOMAIN,
    EMAIL_ENTITY,
    OPT_CRITICAL_ENTITIES,
    OPT_EMAIL_ENTITY,
    OPT_IGNORED_DEVICES,
    OPT_IGNORED_PLATFORMS,
    PLATFORM_LABELS,
)
from .report import SKIP


def _platform_label(platform: str) -> str:
    return PLATFORM_LABELS.get(platform, platform.replace("_", " ").title())


def _ignore_choices(hass) -> tuple[dict[str, str], dict[str, str]]:
    """Platforms and devices that can be ignored, from what is unavailable now."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    platforms = {platform: _platform_label(platform) for platform in DEFAULT_IGNORED_PLATFORMS}
    platforms["music_assistant"] = _platform_label("music_assistant")
    devices: dict[str, str] = {}
    for state in hass.states.async_all():
        if state.state != STATE_UNAVAILABLE or state.domain in SKIP:
            continue
        entry = ent_reg.async_get(state.entity_id)
        platform = entry.platform if entry else state.domain
        platforms[platform] = _platform_label(platform)
        if entry is None or not entry.device_id:
            continue
        device = dev_reg.async_get(entry.device_id)
        if device is None:
            continue
        devices[entry.device_id] = device.name_by_user or device.name or state.name or entry.device_id
    return (
        dict(sorted(platforms.items(), key=lambda item: item[1].casefold())),
        dict(sorted(devices.items(), key=lambda item: item[1].casefold())),
    )


class HouseHealthOptionsFlow(OptionsFlow):
    """Choose what the checklist watches and what it leaves out."""

    def _save(self, updates: dict) -> ConfigFlowResult:
        """Merge one page into the saved options."""
        data = dict(self.config_entry.options)
        data.update(updates)
        return self.async_create_entry(title="", data=data)

    async def async_step_init(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Choose ignore list or email and critical sensors."""
        return self.async_show_menu(step_id="init", menu_options=["ignore", "watch"])

    async def async_step_ignore(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Show the ignore lists."""
        platforms, devices = _ignore_choices(self.hass)
        saved = self.config_entry.options
        if user_input is not None:
            return self._save(
                {
                    OPT_IGNORED_PLATFORMS: user_input.get(OPT_IGNORED_PLATFORMS, []),
                    OPT_IGNORED_DEVICES: user_input.get(OPT_IGNORED_DEVICES, []),
                }
            )
        if OPT_IGNORED_PLATFORMS in saved:
            platform_default = [item for item in saved.get(OPT_IGNORED_PLATFORMS) or [] if item in platforms]
        else:
            platform_default = [item for item in DEFAULT_IGNORED_PLATFORMS if item in platforms]
        if OPT_IGNORED_DEVICES in saved:
            device_default = [item for item in saved.get(OPT_IGNORED_DEVICES) or [] if item in devices]
        else:
            ent_reg = er.async_get(self.hass)
            device_default = [
                device_id
                for device_id, name in devices.items()
                if name.casefold() in DEFAULT_IGNORED_DEVICE_NAMES
            ]
            for state in self.hass.states.async_all():
                if (state.name or "").casefold() not in DEFAULT_IGNORED_DEVICE_NAMES:
                    continue
                entry = ent_reg.async_get(state.entity_id)
                if entry and entry.device_id in devices and entry.device_id not in device_default:
                    device_default.append(entry.device_id)
        for device_id in saved.get(OPT_IGNORED_DEVICES) or []:
            if device_id in devices:
                continue
            device = dr.async_get(self.hass).async_get(device_id)
            if device is None:
                continue
            devices[device_id] = device.name_by_user or device.name or device_id
            device_default.append(device_id)
        schema = vol.Schema(
            {
                vol.Optional(OPT_IGNORED_PLATFORMS, default=platform_default): cv.multi_select(platforms),
                vol.Optional(OPT_IGNORED_DEVICES, default=device_default): cv.multi_select(devices),
            }
        )
        return self.async_show_form(step_id="ignore", data_schema=schema)

    async def async_step_watch(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Choose the email recipient and which critical sensors are watched."""
        saved = self.config_entry.options
        known = {entity_id: label for entity_id, label, _states in CRITICAL}
        choices = dict(sorted(known.items(), key=lambda item: item[1].casefold()))
        if user_input is not None:
            selected = [
                entity_id
                for entity_id in user_input.get(OPT_CRITICAL_ENTITIES, [])
                if entity_id in known
            ]
            return self._save(
                {
                    OPT_EMAIL_ENTITY: user_input[OPT_EMAIL_ENTITY],
                    OPT_CRITICAL_ENTITIES: selected,
                }
            )
        if OPT_CRITICAL_ENTITIES in saved:
            critical_default = [
                entity_id for entity_id in saved.get(OPT_CRITICAL_ENTITIES) or [] if entity_id in known
            ]
        else:
            critical_default = list(known)
        email_default = saved.get(OPT_EMAIL_ENTITY) or EMAIL_ENTITY
        if isinstance(email_default, str) and self.hass.states.get(email_default) is not None:
            email_field = vol.Required(OPT_EMAIL_ENTITY, default=email_default)
        else:
            email_field = vol.Required(OPT_EMAIL_ENTITY)
        schema = vol.Schema(
            {
                email_field: selector({"entity": {"domain": "notify"}}),
                vol.Optional(OPT_CRITICAL_ENTITIES, default=critical_default): cv.multi_select(choices),
            }
        )
        return self.async_show_form(step_id="watch", data_schema=schema)


class HouseHealthConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up one House Health checklist."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HouseHealthOptionsFlow:
        """Options for the ignore list, email, and critical sensors."""
        return HouseHealthOptionsFlow()

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Confirm setup. The schedule and ignore list live in the integration."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title="HA Health Check", data={})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))
