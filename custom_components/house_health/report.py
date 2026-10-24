"""Build the house-health checklist."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
import re

from homeassistant.config_entries import SOURCE_IGNORE, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .const import (
    CRITICAL,
    DEFAULT_IGNORED_DEVICE_NAMES,
    DEFAULT_IGNORED_PLATFORMS,
    EMAIL_ENTITY,
    EXPECTED_OFFLINE_TITLES,
    INTENTIONAL_OFF_AUTOMATIONS,
    OPT_CRITICAL_ENTITIES,
    OPT_EMAIL_ENTITY,
    OPT_IGNORED_DEVICES,
    OPT_IGNORED_PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)

DEAD = {STATE_UNAVAILABLE, STATE_UNKNOWN}
SKIP = {"event", "update", "stt", "tts", "conversation", "button"}
PRIMARY = {"light", "switch", "fan", "media_player", "climate", "lock", "camera"}
SAFETY = {"moisture", "safety", "smoke", "gas", "problem"}
CHRISTMAS_AUTOMATION = "automation.lighting_front_porch_outlet"
_SECRET = re.compile(
    r"(?i)\b(password|passwd|token|api[_-]?key|secret|authorization|bearer)\b\s*[=:]\s*\S+"
)
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:access_token|token|api_key|apikey|password|secret)=)[^&\s]+"
)


@dataclass
class Item:
    """One checklist line."""

    level: str
    text: str


@dataclass
class Report:
    """A finished checklist."""

    title: str
    body: str
    fix_count: int
    check_count: int
    created: datetime


@dataclass
class UnavailableEntity:
    """One unavailable entity on a device."""

    name: str
    entity_id: str
    platform: str


@dataclass
class Bucket:
    """Unavailable entities that belong to one device."""

    device: str
    device_id: str | None = None
    titles: set[str] = field(default_factory=set)
    entities: list[UnavailableEntity] = field(default_factory=list)
    primary: bool = False
    safety: list[str] = field(default_factory=list)


def notify_entity(options: dict | None) -> str:
    """Notify entity that receives the email."""
    value = (options or {}).get(OPT_EMAIL_ENTITY)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return EMAIL_ENTITY


def critical_rows(options: dict | None) -> tuple:
    """Critical sensors to watch. A saved empty list watches none."""
    options = options or {}
    if OPT_CRITICAL_ENTITIES not in options:
        return CRITICAL
    selected = {str(item) for item in options.get(OPT_CRITICAL_ENTITIES) or []}
    return tuple(row for row in CRITICAL if row[0] in selected)


def _aware(value: datetime) -> datetime:
    """Return a timezone-aware datetime."""
    if value.tzinfo is None:
        return dt_util.as_utc(value)
    return value


def _stamp(value: datetime) -> str:
    local = dt_util.as_local(_aware(value))
    hour = str(int(local.strftime("%I")))
    return local.strftime("%A ") + hour + local.strftime(":%M %p")


def _as_int(value, default: int = 1) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number or default


def _optional_int(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _redact(text: str) -> str:
    """Hide credentials that sometimes land in the system log."""
    text = _SECRET.sub(r"\1=[redacted]", text)
    return _QUERY_SECRET.sub(r"\1[redacted]", text)


def _short(text: str, limit: int = 180) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def counts_from_body(body: str) -> tuple[int, int]:
    """Count numbered Fix and Check lines in a saved report."""
    section = None
    fixes = 0
    checks = 0
    for line in body.splitlines():
        stripped = line.strip()
        if stripped == "FIX":
            section = "fix"
            continue
        if stripped == "CHECK":
            section = "check"
            continue
        if stripped in {"LEFT AS-IS", "HEALTHY"}:
            section = None
            continue
        if section and stripped[:1].isdigit() and ". " in stripped[:6]:
            if section == "fix":
                fixes += 1
            else:
                checks += 1
    return fixes, checks


def report_from_values(
    title: str | None,
    body: str | None,
    created: datetime | None,
    fix: int | None = None,
    check: int | None = None,
) -> Report | None:
    """Build a report from stored pieces."""
    if not isinstance(created, datetime):
        return None
    text = body or ""
    parsed_fix, parsed_check = counts_from_body(text)
    return Report(
        title=title or "HA Health Check",
        body=text,
        fix_count=parsed_fix if fix is None else fix,
        check_count=parsed_check if check is None else check,
        created=_aware(created),
    )


def report_from_notification(note: dict) -> Report | None:
    """Build a report from an in-memory persistent notification."""
    created = note.get("created_at")
    if isinstance(created, str):
        created = dt_util.parse_datetime(created)
    if not isinstance(created, datetime):
        return None
    return report_from_values(note.get("title"), note.get("message") or "", created)


def report_from_storage(data: dict) -> Report | None:
    """Build a report from the integration's saved copy."""
    created = data.get("created")
    if isinstance(created, str):
        created = dt_util.parse_datetime(created)
    if not isinstance(created, datetime):
        return None
    return report_from_values(
        data.get("title"),
        data.get("body") or "",
        created,
        _optional_int(data.get("fix")),
        _optional_int(data.get("check")),
    )


def report_from_restored_state(state: State) -> Report | None:
    """Build a report from the recorder's last sensor state."""
    if state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE, "", "unknown", "unavailable"}:
        return None
    created = dt_util.parse_datetime(state.state)
    if created is None:
        return None
    attrs = state.attributes
    return report_from_values(
        attrs.get("title"),
        attrs.get("report") or "",
        created,
        _optional_int(attrs.get("fix")),
        _optional_int(attrs.get("check")),
    )


def _logs(hass: HomeAssistant) -> list:
    """Return system-log records. A private-API change must not cancel the email."""
    try:
        data = hass.data.get("system_log")
        records = getattr(data, "records", None) if data is not None else None
        if isinstance(records, dict):
            return list(records.values())
        if records is None:
            return []
        return list(records)
    except Exception:
        _LOGGER.warning("House health could not read the system log", exc_info=True)
        return []


def _field(record, key, default=""):
    if isinstance(record, dict):
        return record.get(key, default)
    return getattr(record, key, default)


def _text(record) -> tuple[str, str, str, int]:
    message = _field(record, "message", "")
    if isinstance(message, list):
        message = " ".join(str(part) for part in message)
    exception = str(_field(record, "exception", "") or "")
    count = _as_int(_field(record, "count", 1))
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


def _device_ignored(bucket: Bucket, device_ids: set[str], device_names: set[str]) -> bool:
    if bucket.device_id and bucket.device_id in device_ids:
        return True
    if bucket.device.casefold() in device_names:
        return True
    return any(entity.name.casefold() in device_names for entity in bucket.entities)


def _is_phone(bucket: Bucket, entity: UnavailableEntity) -> bool:
    if entity.platform == "mobile_app":
        return True
    return entity.platform == "hubitat" and "iphone" in bucket.device.casefold()


def _skip_reason(bucket: Bucket, entity: UnavailableEntity) -> str | None:
    """Why an unavailable entity stays off the device line, if it does."""
    folded = entity.name.casefold()
    if entity.platform == "alexa_media" and ("shuffle" in folded or "repeat" in folded):
        return "drop"
    if entity.platform == "reolink" and bucket.primary and any(
        token in folded for token in ("volume", "auto focus", "doorbell")
    ):
        return "drop"
    if bucket.primary and ("button" in folded or " level" in folded):
        return "drop"
    if folded.endswith("current h"):
        return "energy"
    if entity.platform == "mqtt" and any(token in folded for token in ("sleep", "restart", "shutdown")):
        return "button"
    return None


def _log_finding(record) -> tuple[str, Item] | None:
    """Classify one log record. Unreadable records are skipped."""
    try:
        kind, name, text, count = _text(record)
    except Exception:
        _LOGGER.debug("House health skipped an unreadable log record", exc_info=True)
        return None
    if kind not in {"error", "warning"}:
        return None
    folded = text.casefold()
    if "elegoo" in name or "centauri" in folded or "grpcio" in folded or "post-quantum" in folded:
        return None
    if "referenced entities fan." in folded or "ejector-pump-alarm" in folded:
        return None
    if "battery_level" in folded and "life360" in folded:
        return (
            "life360",
            Item(
                "ignore",
                "Life360 still reports battery on each person. Home Assistant removes the battery_level property in 2027.7.",
            ),
        )
    if "signal_strength" in folded and "native unit" in folded:
        return (
            "wifi",
            Item(
                "ignore",
                "ESPHome Wi-Fi signal stays in percent on purpose. The readings still work.",
            ),
        )
    if "c4_audio" in name or "192.168.68.93" in folded:
        return (
            "c4",
            Item(
                "check",
                f"Control4 amp at 192.168.68.93 missed {count} poll(s) since startup. A power cycle leaves this in the log.",
            ),
        )
    if "linkplay" in name or "192.168.68.106" in folded:
        return (
            "wiim",
            Item("check", "WiiM at 192.168.68.106 returned an empty reply. Confirm the player is on the network."),
        )
    if "nfandroidtv" in name.casefold() or "nfandroidtv" in folded:
        return (
            "tv",
            Item(
                "check",
                f"A Fire TV notification failed ({count} this boot). The set was off or asleep.",
            ),
        )
    if name == "homeassistant" and "task exception was never retrieved" in folded:
        return None
    line = f"Log {kind} from {name} ({count} this boot): {_redact(text[:220])}"
    return name + text[:80], Item("check", line)


def _repair_line(issue, translations: dict[str, str]) -> str:
    """Human title for a repair, with the internal key as a fallback."""
    placeholders = getattr(issue, "translation_placeholders", None) or {}
    if not isinstance(placeholders, dict):
        placeholders = {}
    safe = {str(key): str(value) for key, value in placeholders.items() if value is not None}
    key = getattr(issue, "translation_key", None)
    template = translations.get(f"component.{issue.domain}.issues.{key}.title") if key else None
    if template:
        try:
            title = template.format(**safe)
        except (IndexError, KeyError, ValueError):
            title = template
        return f"Repair: {_short(title, 300)}."
    addon = safe.get("addon") or safe.get("name")
    if addon and key:
        readable = str(key).removeprefix("issue_").replace("_", " ")
        return f"Repair: {addon} ({readable})."
    label = key or getattr(issue, "issue_id", "unknown")
    return f"Repair: {issue.domain} {label}."


async def _repair_items(hass: HomeAssistant) -> tuple[list[Item], int]:
    active = []
    try:
        issues = ir.async_get(hass).issues.values()
    except Exception:
        _LOGGER.warning("House health could not read repairs", exc_info=True)
        return [], 0
    for issue in issues:
        if getattr(issue, "dismissed", False) or getattr(issue, "active", True) is False:
            continue
        active.append(issue)
    translations: dict[str, str] = {}
    if active:
        try:
            from homeassistant.helpers.translation import async_get_translations

            translations = await async_get_translations(
                hass,
                hass.config.language,
                "issues",
                integrations=sorted({issue.domain for issue in active}),
            )
        except Exception:
            _LOGGER.debug("House health could not load repair titles", exc_info=True)
    items = [Item("fix", _repair_line(issue, translations)) for issue in active]
    items.sort(key=lambda item: item.text.casefold())
    return items, len(active)


def _night_watchman(hass: HomeAssistant, now: datetime) -> tuple[Item | None, str | None]:
    """Return a check item, or a healthy line."""
    try:
        last = hass.states.get("sensor.night_watchman_last_round")
        if last is None:
            return Item("check", "Night Watchman has no last-round sensor."), None
        result = str(last.attributes.get("result") or "").strip()
        stamp = dt_util.parse_datetime(last.state)
        when = _stamp(stamp) if stamp else last.state
        stale = False
        if stamp is not None:
            try:
                stale = now - _aware(stamp) > timedelta(hours=36)
            except (TypeError, ValueError):
                stale = False
        if stale:
            return Item("check", f"Night Watchman last ran {when}: {result or 'no result'}."), None
        if result and not result.startswith("All clear"):
            return Item("check", f"Night Watchman last round at {when}: {result}"), None
        if result:
            return None, f"Night Watchman last round at {when}: {result}"
        return None, None
    except Exception:
        _LOGGER.warning("House health could not read Night Watchman", exc_info=True)
        return Item("check", "Night Watchman last-round sensor could not be read."), None


def _device_line(bucket: Bucket, kept: list[str]) -> str:
    if len(kept) == 1:
        detail = f"{kept[0]} is unavailable."
    else:
        shown = ", ".join(kept[:4])
        if len(kept) > 4:
            shown += f", and {len(kept) - 4} more"
        detail = f"{bucket.device}: {len(kept)} unavailable ({shown})."
    if bucket.safety:
        detail += " Still reporting: " + ", ".join(bucket.safety[:2]) + "."
    return detail


async def async_build_report(hass: HomeAssistant, options: dict | None = None) -> Report:
    """Build one checklist from the current Home Assistant state."""
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
        if entry.state in {
            ConfigEntryState.SETUP_ERROR,
            ConfigEntryState.MIGRATION_ERROR,
            ConfigEntryState.FAILED_UNLOAD,
            ConfigEntryState.SETUP_RETRY,
        }:
            reason = f" {_short(str(entry.reason))}" if entry.reason else ""
            items.append(Item("fix", f"{entry.title} ({entry.domain}) is {entry.state.value}.{reason}"))

    repair_items, repairs = await _repair_items(hass)
    items.extend(repair_items)

    alerts: list[State] = []
    updates: list[str] = []
    automations: list[State] = []
    buckets: dict[str, Bucket] = {}
    live_primary: set[str] = set()
    live_safety: dict[str, list[str]] = {}

    for state in hass.states.async_all():
        if state.entity_id.startswith("alert."):
            alerts.append(state)
        if state.entity_id.startswith("update.") and state.state == "on":
            updates.append(state.name or state.entity_id)
        if state.entity_id.startswith("automation."):
            automations.append(state)
        entity = registry.async_get(state.entity_id)
        device_id = entity.device_id if entity and entity.device_id else None
        if device_id and state.state not in DEAD:
            if state.domain in PRIMARY:
                live_primary.add(device_id)
            if state.attributes.get("device_class") in SAFETY:
                live_safety.setdefault(device_id, []).append(state.name or state.entity_id)
        if state.state != STATE_UNAVAILABLE or state.domain in SKIP:
            continue
        platform = (entity.platform if entity and entity.platform else None) or state.domain
        device = devices.async_get(device_id) if device_id else None
        if device is None:
            key = state.entity_id
            name = state.name or state.entity_id
            entry_titles: set[str] = set()
            bucket_device_id = None
        else:
            key = device.id
            name = device.name_by_user or device.name or state.name or state.entity_id
            bucket_device_id = device.id
            entry_titles = {titles.get(item, "") for item in device.config_entries}
            entry_titles.discard("")
        bucket = buckets.get(key)
        if bucket is None:
            bucket = Bucket(device=name, device_id=bucket_device_id, titles=entry_titles)
            buckets[key] = bucket
        bucket.entities.append(
            UnavailableEntity(state.name or state.entity_id, state.entity_id, platform)
        )

    for bucket in buckets.values():
        if bucket.device_id and bucket.device_id in live_primary:
            bucket.primary = True
        if bucket.device_id:
            bucket.safety = live_safety.get(bucket.device_id, [])

    fix_ids: set[str] = set()
    for state in sorted(alerts, key=lambda item: (item.name or item.entity_id).casefold()):
        if state.state == "on":
            items.append(Item("fix", f"Alert is active: {state.name}."))
        elif state.state in DEAD:
            items.append(Item("fix", f"Alert is unavailable: {state.name}."))
            fix_ids.add(state.entity_id)
    for state in sorted(alerts, key=lambda item: (item.name or item.entity_id).casefold()):
        if state.state == "off":
            items.append(Item("check", f"Alert is acknowledged: {state.name}."))

    ok = []
    for entity_id, label, ok_states in critical_rows(options):
        state = hass.states.get(entity_id)
        if state is None or state.state in DEAD:
            items.append(Item("fix", f"{label} is {state.state if state else 'missing'} ({entity_id})."))
            fix_ids.add(entity_id)
            continue
        if state.state.casefold() not in ok_states:
            items.append(Item("fix", f"{label} is {state.state}."))
            continue
        ok.append(label)

    for state in automations:
        if state.state in DEAD:
            fix_ids.add(state.entity_id)

    phone = False
    wol = False
    energy: set[str] = set()
    buttons: set[str] = set()
    ignored_devices: list[str] = []
    ignored_energy = False
    ignored_platforms, ignored_device_ids, ignored_device_names = _ignore_rules(options)
    for bucket in sorted(buckets.values(), key=lambda item: item.device.casefold()):
        if _device_ignored(bucket, ignored_device_ids, ignored_device_names):
            if bucket.entities and all(entity.platform == "energy" for entity in bucket.entities):
                ignored_energy = True
            else:
                ignored_devices.append(bucket.device)
            continue
        kept: list[str] = []
        for entity in bucket.entities:
            if entity.entity_id in fix_ids:
                continue
            if entity.platform in ignored_platforms:
                if entity.platform == "energy":
                    ignored_energy = True
                continue
            if _is_phone(bucket, entity):
                phone = True
                continue
            if entity.platform == "wake_on_lan":
                wol = True
                continue
            reason = _skip_reason(bucket, entity)
            if reason == "energy":
                energy.add(bucket.device)
                continue
            if reason == "button":
                buttons.add(bucket.device)
                continue
            if reason == "drop":
                continue
            kept.append(entity.name)
        if not kept:
            if bucket.entities and all(entity.platform in ignored_platforms for entity in bucket.entities):
                if not all(entity.platform == "energy" for entity in bucket.entities):
                    ignored_devices.append(bucket.device)
            continue
        kept.sort(key=str.casefold)
        if bucket.titles and bucket.titles <= EXPECTED_OFFLINE_TITLES:
            label = ", ".join(sorted(bucket.titles))
            noun = "entity" if len(kept) == 1 else "entities"
            items.append(Item("ignore", f"{label} is powered off on purpose ({len(kept)} {noun} unavailable)."))
            continue
        items.append(Item("check", _device_line(bucket, kept)))

    if energy:
        items.append(Item("check", "Hubitat energy sensors are unavailable: " + ", ".join(sorted(energy)) + "."))
    if buttons:
        items.append(
            Item(
                "check",
                "PC sleep, restart, and shutdown buttons are unavailable: " + ", ".join(sorted(buttons)) + ".",
            )
        )
    if phone:
        items.append(
            Item(
                "ignore",
                "iPhone companion and Hubitat phone-presence sensors are ignored. Life360 is the presence source.",
            )
        )
    if wol:
        items.append(
            Item("ignore", "Wake-on-LAN switches are ignored. They show unavailable until a wake packet is sent.")
        )
    ignored_bits: list[str] = []
    if ignored_energy:
        ignored_bits.append("Energy cost sensors")
    if ignored_devices:
        ignored_bits.append(", ".join(sorted(set(ignored_devices))))
    if ignored_bits:
        items.append(Item("ignore", "Ignored: " + "; ".join(ignored_bits) + "."))

    seen: set[str] = set()
    for record in _logs(hass):
        finding = _log_finding(record)
        if finding is None:
            continue
        key, item = finding
        if key in seen:
            continue
        seen.add(key)
        items.append(item)

    if updates:
        names = sorted(set(updates))
        shown = ", ".join(names[:8])
        if len(names) > 8:
            shown += f", and {len(names) - 8} more"
        items.append(Item("check", f"Updates available: {shown}."))

    watchman_item, watchman_healthy = _night_watchman(hass, now)
    if watchman_item is not None:
        items.append(watchman_item)
    elif watchman_healthy:
        healthy.append(watchman_healthy)

    off = []
    for state in automations:
        if state.entity_id in INTENTIONAL_OFF_AUTOMATIONS:
            if state.state in DEAD:
                items.append(Item("fix", f"Automation is unavailable: {state.name or state.entity_id}."))
                fix_ids.add(state.entity_id)
            continue
        if state.state == "off":
            off.append(state.name or state.entity_id)
        elif state.state in DEAD:
            items.append(Item("fix", f"Automation is unavailable: {state.name or state.entity_id}."))
            fix_ids.add(state.entity_id)
    if off:
        items.append(Item("check", "Automations turned off: " + ", ".join(sorted(set(off))) + "."))
    christmas = hass.states.get(CHRISTMAS_AUTOMATION)
    if (
        CHRISTMAS_AUTOMATION in INTENTIONAL_OFF_AUTOMATIONS
        and christmas is not None
        and christmas.state == "off"
    ):
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
    lines = [
        "HA Health Check",
        _stamp(now),
        "",
        "Fix means something that should be working is broken. Check means look when you have a minute.",
        "",
    ]
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
    return Report(
        title=title,
        body="\n".join(lines).strip() + "\n",
        fix_count=len(fixes),
        check_count=len(checks),
        created=now,
    )
