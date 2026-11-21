"""Report parsing and option helpers."""

from datetime import UTC, datetime

from custom_components.house_health.const import CRITICAL, EMAIL_ENTITY, OPT_CRITICAL_ENTITIES, OPT_EMAIL_ENTITY
from custom_components.house_health.report import (
    _redact,
    _short,
    counts_from_body,
    critical_rows,
    notify_entity,
    report_from_storage,
)

BODY = """HA Health Check

FIX
1. Kitchen motion sensor is unavailable.
2. Garage door battery is low.

CHECK
1. Night Watchman last round at Sunday 2:00 AM: Still open

HEALTHY
1. Backups are current.
"""


def test_counts_only_numbered_fix_and_check_lines():
    assert counts_from_body(BODY) == (2, 1)


def test_counts_empty_body():
    assert counts_from_body("") == (0, 0)


def test_report_from_storage_prefers_saved_counts():
    report = report_from_storage(
        {"title": "T", "body": BODY, "fix": 5, "check": 0, "created": "2026-11-15T07:00:00+00:00"}
    )
    assert report is not None
    assert (report.fix_count, report.check_count) == (5, 0)
    assert report.created == datetime(2026, 11, 15, 7, tzinfo=UTC)


def test_report_from_storage_parses_counts_when_missing():
    report = report_from_storage({"body": BODY, "created": "2026-11-15T07:00:00+00:00"})
    assert report is not None
    assert report.title == "HA Health Check"
    assert (report.fix_count, report.check_count) == (2, 1)


def test_report_from_storage_rejects_bad_timestamp():
    assert report_from_storage({"body": BODY, "created": "not a date"}) is None
    assert report_from_storage({}) is None


def test_notify_entity_default_and_override():
    assert notify_entity(None) == EMAIL_ENTITY
    assert notify_entity({OPT_EMAIL_ENTITY: "  notify.me  "}) == "notify.me"
    assert notify_entity({OPT_EMAIL_ENTITY: "   "}) == EMAIL_ENTITY


def test_critical_rows_saved_empty_list_watches_none():
    assert critical_rows({}) == CRITICAL
    assert critical_rows({OPT_CRITICAL_ENTITIES: []}) == ()
    first = CRITICAL[0][0]
    assert critical_rows({OPT_CRITICAL_ENTITIES: [first]}) == (CRITICAL[0],)


def test_redact_hides_credentials():
    text = _redact("login failed password=hunter2 url=http://x/api?access_token=abc&x=1")
    assert "hunter2" not in text
    assert "abc" not in text
    assert "&x=1" in text


def test_short_collapses_whitespace_and_truncates():
    assert _short("a   b\n c") == "a b c"
    out = _short("x" * 300, limit=20)
    assert len(out) == 20 and out.endswith("...")


def test_life360_row_is_a_pattern_and_matches_saved_exact_ids():
    from custom_components.house_health.report import critical_key

    life360 = next(row for row in CRITICAL if row[1] == "Life360")
    assert "*" in life360[0]
    # An options save from 1.2.1 stored the exact sensor id; it still selects the row.
    assert critical_key("binary_sensor.life360_online_someone_example_com") == life360[0]
    assert critical_rows({OPT_CRITICAL_ENTITIES: ["binary_sensor.life360_online_someone_example_com"]}) == (life360,)
    assert critical_key("binary_sensor.not_critical") is None


def test_no_default_email_recipient():
    assert EMAIL_ENTITY == ""
    assert notify_entity(None) == ""
