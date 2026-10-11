"""Build the house-health checklist."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from homeassistant.config_entries import SOURCE_IGNORE, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from .const import (
    CRITICAL,
    DEFAULT_IGNORED_DEVICE_NAMES,
    DEFAULT_IGNORED_PLATFORMS,
    EXPECTED_OFFLINE_TITLES,
    INTENTIONAL_OFF_AUTOMATIONS,
    OPT_IGNORED_DEVICES,
    OPT_IGNORED_PLATFORMS,
)
DEAD = {STATE_UNAVAILABLE, STATE_UNKNOWN}
SKIP = {"event", "update", "stt", "tts", "conversation", "button"}
PRIMARY = {"light", "switch", "fan", "media_player", "climate", "lock", "camera"}
@dataclass
class Item:
    level: str
    text: str
@dataclass
class Report:
    title: str
    body: str
    fix_count: int
    check_count: int
    created: datetime
@dataclass
class Bucket:
    device: str
    platform: str
    device_id: str | None = None
    names: list[str] = field(default_factory=list)
    titles: set[str] = field(default_factory=set)
    primary: bool = False
    safety: list[str] = field(default_factory=list)
def _stamp(value: datetime) -> str:
    local = dt_util.as_local(value)
    hour = str(int(local.strftime("%I")))
    return local.strftime("%A ") + hour + local.strftime(":%M %p")
def _logs(hass: HomeAssistant):
    data = hass.data.get("system_log")
    records = getattr(data, "records", None) if data is not None else None
    if isinstance(records, dict):
        return list(records.values())
    return list(records or [])
def _field(record, key, default=""):
    if isinstance(record, dict):
        return record.get(key, default)
    return getattr(record, key, default)

def _text(record):
    message = _field(record, "message", "")
    if isinstance(message, list):
        message = " ".join(str(part) for part in message)
    exception = str(_field(record, "exception", "") or "")
    count = int(_field(record, "count", 1) or 1)
    level = _field(record, "level", 0)
    name = str(_field(record, "name", "") or "")
    if isinstance(level, int):
        if level >= 40:
            kind = "error"
        elif level >= 30:
            kind = "warning"
        else:
            kind = "info"
    else:
        kind = str(level).casefold()
    return kind, name, " ".join(f"{message} {exception}".split()), count
def _ignore_rules(options: dict | None) -> tuple[set[str], set[str], set[str]]:
    """Platforms, device ids, and device names that stay off the checklist."""
    options = options or {}
    if OPT_IGNORED_PLATFORMS in options:
        platforms = {str(item) for item in options.get(OPT_IGNORED_PLATFORMS) or []}
    else:
        platforms = set(DEFAULT_IGNORED_PLATFORMS)
    if OPT_IGNORED_DEVICES in options:
        return platforms, {str(item) for item in options.get(OPT_IGNORED_DEVICES) or []}, set()
    return platforms, set(), set(DEFAULT_IGNORED_DEVICE_NAMES)


def _bucket_ignored(bucket: Bucket, platforms: set[str], device_ids: set[str], device_names: set[str]) -> bool:
    if bucket.platform in platforms:
        return True
    if bucket.device_id and bucket.device_id in device_ids:
        return True
    if bucket.device.casefold() in device_names:
        return True
    return any(name.casefold() in device_names for name in bucket.names)


async def async_build_report(hass: HomeAssistant, options: dict | None = None) -> Report:
    now = dt_util.utcnow()
    items: list[Item] = []
    healthy: list[str] = []
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    titles = {entry.entry_id: entry.title for entry in hass.config_entries.async_entries()}
    ignored = 0
    loaded = 0
    for entry in hass.config_entries.async_entries():
        if entry.state == ConfigEntryState.LOADED:
            loaded += 1
        if entry.state == ConfigEntryState.NOT_LOADED and entry.source == SOURCE_IGNORE:
            ignored += 1
            continue
        if entry.state == ConfigEntryState.SETUP_RETRY and entry.title in EXPECTED_OFFLINE_TITLES:
            continue
        if entry.state in {ConfigEntryState.SETUP_ERROR, ConfigEntryState.MIGRATION_ERROR, ConfigEntryState.FAILED_UNLOAD, ConfigEntryState.SETUP_RETRY}:
            reason = f" {entry.reason}" if entry.reason else ""
            items.append(Item("fix", f"{entry.title} ({entry.domain}) is {entry.state.value}.{reason}"))
    issues = ir.async_get(hass)
    repairs = 0
    for issue in issues.issues.values():
        if getattr(issue, "dismissed", False) or getattr(issue, "active", True) is False:
            continue
        repairs += 1
        label = issue.translation_key or issue.issue_id
        items.append(Item("fix", f"Repair: {issue.domain} {label}."))
    alerts = [state for state in hass.states.async_all() if state.entity_id.startswith("alert.")]
    for state in alerts:
        if state.state == "on":
            items.append(Item("fix", f"Alert is active: {state.name}."))
        elif state.state in DEAD:
            items.append(Item("fix", f"Alert is unavailable: {state.name}."))
    ok = []
    for entity_id, label, ok_states in CRITICAL:
        state = hass.states.get(entity_id)
        if state is None or state.state in DEAD:
            items.append(Item("fix", f"{label} is {state.state if state else 'missing'} ({entity_id})."))
            continue
        if state.state.casefold() not in ok_states:
            items.append(Item("fix", f"{label} is {state.state}."))
            continue
        ok.append(label)
    buckets: dict[str, Bucket] = {}
    for state in hass.states.async_all():
        if state.state != STATE_UNAVAILABLE or state.domain in SKIP:
            continue
        entity = registry.async_get(state.entity_id)
        platform = entity.platform if entity else state.domain
        device = devices.async_get(entity.device_id) if entity and entity.device_id else None
        if device is None:
            key = state.entity_id
            name = state.name or state.entity_id
            device_id = None
            entry_titles = set()
        else:
            key = device.id
            name = device.name_by_user or device.name or state.name
            device_id = device.id
            entry_titles = {titles.get(item, "") for item in device.config_entries}
            entry_titles.discard("")
        bucket = buckets.get(key)
        if bucket is None:
            bucket = Bucket(device=name, platform=platform, device_id=device_id, titles=entry_titles)
            buckets[key] = bucket
        bucket.names.append(state.name or state.entity_id)
    for state in hass.states.async_all():
        entity = registry.async_get(state.entity_id)
        if entity is None or not entity.device_id or state.state in DEAD:
            continue
        bucket = buckets.get(entity.device_id)
        if bucket is None:
            continue
        if state.domain in PRIMARY:
            bucket.primary = True
        if state.attributes.get("device_class") in {"moisture", "safety", "smoke", "gas", "problem"}:
            bucket.safety.append(state.name or state.entity_id)
    phone = False
    wol = False
    energy = []
    buttons = []
    ignored_devices: list[str] = []
    ignored_energy = False
    ignored_platforms, ignored_device_ids, ignored_device_names = _ignore_rules(options)
    for bucket in buckets.values():
        if _bucket_ignored(bucket, ignored_platforms, ignored_device_ids, ignored_device_names):
            if bucket.platform == "energy":
                ignored_energy = True
            else:
                ignored_devices.append(bucket.device)
            continue
        if bucket.platform == "mobile_app" or (bucket.platform == "hubitat" and "iphone" in bucket.device.casefold()):
            phone = True
            continue
        if bucket.platform == "wake_on_lan":
            wol = True
            continue
        kept = []
        for name in bucket.names:
            folded = name.casefold()
            if bucket.platform == "alexa_media" and ("shuffle" in folded or "repeat" in folded):
                continue
            if bucket.platform == "reolink" and bucket.primary and any(token in folded for token in ("volume", "auto focus", "doorbell")):
                continue
            if bucket.primary and ("button" in folded or " level" in folded):
                continue
            if folded.endswith("current h"):
                energy.append(bucket.device)
                continue
            if bucket.platform == "mqtt" and any(token in folded for token in ("sleep", "restart", "shutdown")):
                buttons.append(bucket.device)
                continue
            kept.append(name)
        if not kept:
            continue
        if bucket.titles and bucket.titles <= EXPECTED_OFFLINE_TITLES:
            label = ", ".join(sorted(bucket.titles))
            noun = "entity" if len(kept) == 1 else "entities"
            items.append(Item("ignore", f"{label} is powered off on purpose ({len(kept)} {noun} unavailable)."))
            continue
        if len(kept) == 1:
            detail = f"{kept[0]} is unavailable."
        else:
            shown = ", ".join(kept[:4])
            if len(kept) > 4:
                shown += f", and {len(kept) - 4} more"
            detail = f"{bucket.device}: {len(kept)} unavailable ({shown})."
        if bucket.safety:
            detail += " Still reporting: " + ", ".join(bucket.safety[:2]) + "."
        items.append(Item("check", detail))
    if energy:
        items.append(Item("check", "Hubitat energy sensors are unavailable: " + ", ".join(sorted(set(energy))) + "."))
    if buttons:
        items.append(Item("check", "PC sleep, restart, and shutdown buttons are unavailable: " + ", ".join(sorted(set(buttons))) + "."))
    if phone:
        items.append(Item("ignore", "iPhone companion and Hubitat phone-presence sensors are ignored. Life360 is the presence source."))
    if wol:
        items.append(Item("ignore", "Wake-on-LAN switches are ignored. They show unavailable until a wake packet is sent."))
    ignored_bits: list[str] = []
    if ignored_energy:
        ignored_bits.append("Energy cost sensors")
    if ignored_devices:
        ignored_bits.append(", ".join(sorted(set(ignored_devices))))
    if ignored_bits:
        items.append(Item("ignore", "Ignored: " + "; ".join(ignored_bits) + "."))
    seen = set()
    for record in _logs(hass):
        kind, name, text, count = _text(record)
        if kind not in {"error", "warning"}:
            continue
        folded = text.casefold()
        level = "check"
        if "elegoo" in name or "centauri" in folded or "grpcio" in folded or "post-quantum" in folded:
            continue
        if "referenced entities fan." in folded or "ejector-pump-alarm" in folded:
            continue
        if "battery_level" in folded and "life360" in folded:
            key = "life360"
            level = "ignore"
            line = "Life360 still reports battery on each person. Home Assistant removes the battery_level property in 2027.7."
        elif "signal_strength" in folded and "native unit" in folded:
            key = "wifi"
            level = "ignore"
            line = "ESPHome Wi-Fi signal stays in percent on purpose. The readings still work."
        elif "c4_audio" in name or "192.168.68.93" in folded:
            key = "c4"
            line = f"Control4 amp at 192.168.68.93 missed {count} poll(s) since startup. A power cycle leaves this in the log."
        elif "linkplay" in name or "192.168.68.106" in folded:
            key = "wiim"
            line = "WiiM at 192.168.68.106 returned an empty reply. Confirm the player is on the network."
        elif "nfandroidtv" in folded or "failed to connect to host" in folded:
            key = "tv"
            line = f"A Fire TV notification failed ({count} this boot). The set was off or asleep."
        elif name == "homeassistant" and "task exception was never retrieved" in folded:
            continue
        else:
            key = name + text[:80]
            line = f"Log {kind} from {name} ({count} this boot): {text[:220]}"
        if key in seen:
            continue
        seen.add(key)
        items.append(Item(level, line))
    updates = [state.name or state.entity_id for state in hass.states.async_all() if state.entity_id.startswith("update.") and state.state == "on"]
    if updates:
        items.append(Item("check", "Updates available: " + ", ".join(sorted(updates)[:8]) + "."))
    last = hass.states.get("sensor.night_watchman_last_round")
    if last is None:
        items.append(Item("check", "Night Watchman has no last-round sensor."))
    else:
        result = str(last.attributes.get("result") or "").strip()
        stamp = dt_util.parse_datetime(last.state)
        when = _stamp(stamp) if stamp else last.state
        if stamp and dt_util.utcnow() - stamp > timedelta(hours=36):
            items.append(Item("check", f"Night Watchman last ran {when}: {result or 'no result'}."))
        elif result and not result.startswith("All clear"):
            items.append(Item("check", f"Night Watchman last round at {when}: {result}"))
        elif result:
            healthy.append(f"Night Watchman last round at {when}: {result}")
    off = []
    for state in hass.states.async_all():
        if not state.entity_id.startswith("automation.") or state.entity_id in INTENTIONAL_OFF_AUTOMATIONS:
            continue
        if state.state == "off":
            off.append(state.name or state.entity_id)
        elif state.state in DEAD:
            items.append(Item("fix", f"Automation is unavailable: {state.name or state.entity_id}."))
    if off:
        items.append(Item("check", "Automations turned off: " + ", ".join(sorted(off)) + "."))
    items.append(Item("ignore", "Christmas lights automation is off until the season."))
    if ignored:
        items.append(Item("ignore", f"{ignored} discovered integrations are ignored and were never set up."))
    email = hass.states.get("notify.email_group")
    members = list(email.attributes.get("entity_id") or []) if email else []
    if email is None or len(members) < 3:
        items.append(Item("fix", "Email group is missing or has fewer than 3 recipients."))
    else:
        healthy.append(f"Email group has {len(members)} recipients.")
    if ok:
        healthy.append("Up: " + ", ".join(ok) + ".")
    idle = [state for state in alerts if state.state == "idle"]
    if alerts and len(idle) == len(alerts):
        healthy.append(f"All {len(alerts)} alerts are idle.")
    healthy.append(f"{loaded} integrations loaded. {repairs} open repairs.")
    fixes = [item.text for item in items if item.level == "fix"]
    checks = [item.text for item in items if item.level == "check"]
    left = [item.text for item in items if item.level == "ignore"]
    if fixes and checks:
        title = f"HA Health Check: {len(fixes)} to fix, {len(checks)} to check"
    elif fixes:
        title = f"HA Health Check: {len(fixes)} to fix"
    elif checks:
        title = f"HA Health Check: {len(checks)} to check"
    else:
        title = "HA Health Check: nothing to fix"
    lines = ["HA Health Check", _stamp(now), "", "Fix means something that should be working is broken. Check means look when you have a minute.", ""]
    if not fixes and not checks:
        lines.extend(["Nothing to fix or check.", ""])
    if fixes:
        lines.append("FIX")
        lines.extend(f"{index}. {text}" for index, text in enumerate(fixes, start=1))
        lines.append("")
    if checks:
        lines.append("CHECK")
        lines.extend(f"{index}. {text}" for index, text in enumerate(checks, start=1))
        lines.append("")
    if left:
        lines.append("LEFT AS-IS")
        lines.extend(f"- {text}" for text in left)
        lines.append("")
    if healthy:
        lines.append("HEALTHY")
        lines.extend(f"- {text}" for text in healthy)
        lines.append("")
    lines.append("Next scheduled report is Sunday at 9:00 AM. Press HA Health Check, Run report, to run it sooner.")
    return Report(title=title, body="\n".join(lines).strip() + "\n", fix_count=len(fixes), check_count=len(checks), created=now)