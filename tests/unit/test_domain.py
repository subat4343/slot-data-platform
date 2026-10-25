from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from slot_data.adapters.base import ParseContext
from slot_data.adapters.manual.parsers import ManualCsvParser, ManualJsonParser, parse_int_text
from slot_data.domain.enums import FieldStatus
from slot_data.domain.ids import uuid7, uuid7_timestamp_ms
from slot_data.domain.timeutil import business_date_of, business_day_start, closing_moment
from slot_data.normalization.persist import event_keys
from slot_data.normalization.validate import validate_bonus_series, validate_metrics, validate_slump_series
from slot_data.observability.logging import redact
from slot_data.schemas.parsed import (
    ParsedBonusEvent,
    ParsedBonusSeries,
    ParsedField,
    ParsedObservation,
    ParsedSlumpSeries,
    normalize_machine_number,
)

JST = ZoneInfo("Asia/Tokyo")
CTX = ParseContext("manual", "H001", "Asia/Tokyo", time(5, 0), datetime(2026, 10, 21, tzinfo=UTC))


def test_uuid7_is_monotonic_and_versioned():
    ids = [uuid7() for _ in range(5000)]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)
    assert all(u.version == 7 and u.variant == "specified in RFC 4122" for u in ids[:10])
    assert abs(uuid7_timestamp_ms(ids[0]) - datetime.now(UTC).timestamp() * 1000) < 5000


def test_business_date_with_cutoff():
    assert business_date_of(datetime(2026, 10, 21, 4, 59, tzinfo=JST), cutoff=time(5)) == date(2026, 10, 20)
    assert business_date_of(datetime(2026, 10, 21, 5, 0, tzinfo=JST), cutoff=time(5)) == date(2026, 10, 21)
    # UTC で与えても店舗ローカルで判定
    assert business_date_of(datetime(2026, 10, 20, 19, 0, tzinfo=UTC), cutoff=time(5)) == date(2026, 10, 20)
    assert business_day_start(date(2026, 10, 20), cutoff=time(5)) == datetime(2026, 10, 19, 20, 0, tzinfo=UTC)
    # 閉店が境界より前（深夜営業）は翌日扱い
    assert closing_moment(date(2026, 10, 20), time(0, 30), cutoff=time(5)) == datetime(
        2026, 10, 20, 15, 30, tzinfo=UTC
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1,234", 1234),
        ("+1,234", 1234),
        ("−567", -567),
        ("1234枚", 1234),
        ("3,000G", 3000),
        ("1.5", None),
        ("abc", None),
    ],
)
def test_parse_int_text(raw, expected):
    assert parse_int_text(raw) == expected


def test_machine_number_normalization():
    assert normalize_machine_number("０３０１") == "301"
    assert normalize_machine_number(" 12番 ") == "12"
    assert normalize_machine_number("A-12") == "A-12"
    with pytest.raises(ValueError):
        normalize_machine_number("  ")


def test_parsed_field_status_consistency():
    with pytest.raises(ValueError):
        ParsedField(status=FieldStatus.OBSERVED)
    with pytest.raises(ValueError):
        ParsedField(status=FieldStatus.NOT_PUBLISHED, value=0)
    assert ParsedField(status=FieldStatus.OBSERVED, value=0).value == 0


def test_csv_parser_distinguishes_missing_and_zero():
    data = (
        b"machine_number,observed_at,total_games,bb_count,rb_count,at_count,cz_count\n"
        b'301,2026-10-20T23:00:00,"1,234",0,,#fetch_failed,abc\n'
        b",2026-10-20T23:00:00,1,1,1,1,1\n"
    )
    result = ManualCsvParser().parse(data, CTX)
    assert len(result.observations) == 1 and len(result.issues) == 1
    fields = result.observations[0].fields
    assert fields["total_games"].value == 1234 and fields["total_games"].raw == "1,234"
    assert fields["bb_count"].value == 0 and fields["bb_count"].status == FieldStatus.OBSERVED
    assert fields["rb_count"].status == FieldStatus.NOT_PUBLISHED
    assert fields["at_count"].status == FieldStatus.FETCH_FAILED
    assert fields["cz_count"].status == FieldStatus.UNPARSEABLE and fields["cz_count"].raw == "abc"
    # naive な時刻は店舗タイムゾーン
    assert result.observations[0].observed_at.utcoffset().total_seconds() == 9 * 3600


def test_json_parser_rejects_other_hall_and_bad_format():
    assert ManualJsonParser().parse(b'{"format":"x"}', CTX).issues[0].code == "UNKNOWN_FORMAT"
    doc = b'{"format":"slot-data/manual-v1","hall_code":"OTHER","machines":[]}'
    assert ManualJsonParser().parse(doc, CTX).issues[0].code == "HALL_MISMATCH"


def _obs(**fields):
    return ParsedObservation(
        source_machine_number="1",
        observed_at=datetime(2026, 10, 20, 12, tzinfo=JST),
        fields={k: ParsedField(status=FieldStatus.OBSERVED, value=v) for k, v in fields.items()},
    )


def test_validate_metrics():
    start = business_day_start(date(2026, 10, 20), cutoff=time(5))
    codes = {
        f.code for f in validate_metrics(_obs(total_games=-1, normal_games=10, net_medals=-99999), start)
    }
    assert codes == {"NEGATIVE_VALUE", "NORMAL_GT_TOTAL", "OUT_OF_RANGE"}
    early = business_day_start(date(2026, 10, 21), cutoff=time(5))
    assert {f.code for f in validate_metrics(_obs(total_games=1), early)} == {"BUSINESS_DATE_MISMATCH"}
    assert validate_metrics(_obs(net_medals=-5000), start) == []  # 差枚の負値は正常


def test_validate_bonus_and_slump():
    series = ParsedBonusSeries(
        status="complete",
        events=[
            ParsedBonusEvent(ordinal=1, cumulative_games=500, raw_label="BIG"),
            ParsedBonusEvent(ordinal=2, cumulative_games=300, raw_label="REG"),
        ],
    )
    assert [f.code for f in validate_bonus_series(series)] == ["BONUS_ORDER_INCONSISTENT"]
    negative = ParsedBonusSeries(
        status="complete",
        events=[
            ParsedBonusEvent(ordinal=1, hit_games=61, raw_label="REG", payout_medals=-7),
            ParsedBonusEvent(ordinal=2, hit_games=-3, raw_label="BIG"),
        ],
    )
    findings = validate_bonus_series(negative)
    # 出枚のマイナスは正当な値。ゲーム数のマイナスは警告（観測全体は隔離しない）
    assert [(f.code, f.field, f.effective_severity) for f in findings] == [
        ("NEGATIVE_VALUE", "hit_games", "warning")
    ]
    with pytest.raises(ValueError):
        ParsedBonusSeries(status="complete", events=[ParsedBonusEvent(ordinal=1, raw_label="a")] * 2)
    unknown = ParsedSlumpSeries(x_axis_type="unknown", y_axis_type="net_medals", points=[])
    assert [f.code for f in validate_slump_series(unknown)] == ["SLUMP_AXIS_UNKNOWN"]
    back = ParsedSlumpSeries(
        x_axis_type="cumulative_games",
        y_axis_type="net_medals",
        y_unit="medals",
        points=[{"x_numeric": 2}, {"x_numeric": 1}],
    )
    assert [f.code for f in validate_slump_series(back)] == ["SLUMP_X_NOT_MONOTONIC"]


def test_event_keys_stable_and_unique():
    events = [
        ParsedBonusEvent(ordinal=1, hit_games=10, raw_label="BIG"),
        ParsedBonusEvent(ordinal=2, hit_games=10, raw_label="BIG"),
        ParsedBonusEvent(ordinal=3, source_event_id="e9", raw_label="REG"),
    ]
    keys = event_keys(events)
    assert keys[0] != keys[1] and keys[0].split("#")[0] == keys[1].split("#")[0]
    assert keys[2] == "id:e9"
    assert event_keys(events) == keys


def test_log_redaction():
    assert redact("url=postgresql://u:secret@h/db token=abc") == "url=postgresql://u:***@h/db token=***"
