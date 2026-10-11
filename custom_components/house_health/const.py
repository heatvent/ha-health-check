"""Constants for the House Health checklist."""

from __future__ import annotations

DOMAIN = "house_health"

SIGNAL_REPORT = "house_health_report_updated"
NOTIFICATION_ID = "house_health"

# Sunday 9:00 AM local, after Quiet Mode turns off at 7:00 AM.
REPORT_WEEKDAY = 6
REPORT_HOUR = 9
REPORT_MINUTE = 0

EMAIL_ENTITY = "notify.gmail_smtp_matteocp_gmail_com"

# Integrations that are expected to sit in setup_retry while powered off.
EXPECTED_OFFLINE_TITLES = frozenset(
    {
        "Centauri Carbon",
        "Office TV",
        "Master Bathroom TV",
    }
)

# Turned off on purpose outside the holiday season.
INTENTIONAL_OFF_AUTOMATIONS = frozenset(
    {
        "automation.lighting_front_porch_outlet",
    }
)

# A missing option means use the default. An empty list means it was cleared on purpose.
OPT_IGNORED_PLATFORMS = "ignored_platforms"
OPT_IGNORED_DEVICES = "ignored_devices"

# No energy price is configured, so these cost sensors stay unavailable.
DEFAULT_IGNORED_PLATFORMS = ("energy",)

# Music Assistant copies of rooms played through the Control4 amp.
# The Control4 players stay on the checklist.
DEFAULT_IGNORED_DEVICE_NAMES = frozenset(
    {
        "bar speakers (ma)",
        "dining room speakers (ma)",
        "garage speakers (ma)",
        "kitchen speakers (ma)",
        "master bathroom speakers (ma)",
        "master bedroom speakers (ma)",
        "patio speakers (ma)",
    }
)

PLATFORM_LABELS = {
    "energy": "Energy cost sensors",
    "music_assistant": "Music Assistant",
    "hubitat": "Hubitat",
    "esphome": "ESPHome",
    "alexa_media": "Alexa and Fire TV",
    "reolink": "Reolink",
    "mobile_app": "Mobile app",
    "wake_on_lan": "Wake on LAN",
    "nfandroidtv": "Fire TV notifications",
    "mqtt": "MQTT",
}

# entity_id, label, states that mean healthy. Any other live state is a fix.
CRITICAL = (
    ("binary_sensor.hubitat_elevation_hub_status", "Hubitat", frozenset({"on"})),
    ("binary_sensor.life360_online_matteocp_gmail_com", "Life360", frozenset({"on"})),
    ("binary_sensor.rachio", "Rachio", frozenset({"on"})),
    ("sensor.server_closet_cyberpower_ups_status", "UPS", frozenset({"online"})),
    (
        "alarm_control_panel.caddx_nx_8e",
        "Caddx panel",
        frozenset({"disarmed", "armed_home", "armed_away", "armed_night", "armed_vacation", "arming", "pending"}),
    ),
    ("binary_sensor.water_sensor_group", "Water sensor group", frozenset({"off"})),
    ("binary_sensor.sump_pump_alarm_sump_pump_alarm_high_water_alarm", "Sump high-water sensor", frozenset({"off"})),
    ("binary_sensor.ejector_pump_water_water", "Ejector leak sensor", frozenset({"off"})),
    ("binary_sensor.attic_freeze_sensor", "Attic freeze sensor", frozenset({"off"})),
    ("switch.night_watchman_enabled", "Night Watchman", frozenset({"on"})),
)
