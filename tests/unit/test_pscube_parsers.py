"""P's CUBE 画面パーサー（2026-10-23 に保存した実画面 HTML を使用）。"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from slot_data.adapters.base import ParseContext
from slot_data.adapters.pscube.parsers import (
    MachineDetailParser,
    ModelListParser,
    ModelPageParser,
    page_error_code,
    parse_machine_links,
    parse_model_list,
    parse_updated_at,
)
from slot_data.domain.enums import FieldStatus
from tests.conftest import FIXTURES

D = FIXTURES / "pscube"
MODEL_URL = "https://www.pscube.jp/dedamajyoho-P-townDMMpachi/c743201/cgi-bin/nc-v05-003.php?cd_ps=2&bai=21.277&nmk_kisyu=x"
CTX = ParseContext(
    source_code="pscube_list",
    hall_code="H001",
    timezone="Asia/Tokyo",
    business_day_cutoff=time(5, 0),
    fetched_at=datetime(2026, 10, 23, 8, 40, tzinfo=UTC),
    resource_locator=MODEL_URL,
)


def _read(name: str) -> str:
    return (D / name).read_text(encoding="utf-8")


def test_model_list():
    models = parse_model_list(_read("model_list.html"))
    assert len(models) == 20
    first = models[0]
    assert first.name == "マイジャグラーⅥ"
    assert first.rate_label == "21.3スロ" and first.bai == "21.277" and first.machine_count == 3
    assert first.href.startswith("nc-v05-003.php?")
    assert any(m.bai == "5.319" for m in models)  # 5 円スロ
    result = ModelListParser().parse(_read("model_list.html").encode(), CTX)
    assert result.observations == [] and result.issues == []


def test_model_page_daily_bonus_counts():
    html = _read("model_page_myjuggler6.html")
    assert parse_machine_links(html)[0] == ("0277", "nc-v06-001.php?cd_dai=0277")
    assert parse_updated_at(html, "Asia/Tokyo") == datetime(
        2026, 10, 23, 17, 35, tzinfo=ZoneInfo("Asia/Tokyo")
    )

    result = ModelPageParser().parse(html.encode(), CTX)
    assert not [i for i in result.issues if i.severity in ("error", "critical")]
    by_key = {(o.source_machine_number, o.business_date): o for o in result.observations}
    # 本日〜4日前に値があり、5・6日前は空欄（観測を作らない）
    assert len(result.observations) == 3 * 5
    today = by_key[("0277", date(2026, 10, 23))]
    assert today.fields["bonus_count"].value == 39
    assert today.fields["bonus_count"].status == FieldStatus.OBSERVED
    assert today.fields["bonus_count"].label == "BONUS"
    assert today.source_model_name == "マイジャグラーⅥ"
    assert today.observed_at == CTX.fetched_at
    assert today.extra["bai"] == "21.277"
    assert by_key[("0277", date(2026, 10, 22))].fields["bonus_count"].value == 48
    assert by_key[("0280", date(2026, 10, 19))].fields["bonus_count"].value == 13
    assert ("0277", date(2026, 10, 18)) not in by_key
    # BIG/REG の内訳は機種画面に無いので作らない
    assert set(today.fields) == {"bonus_count"}


def test_machine_detail_daily_and_history():
    # 10/26 05:34 JST に開いた画面。更新時刻（10/25 22:14）から「本日」= 10/25 と判定する
    ctx = ParseContext(
        source_code="pscube",
        hall_code="H001",
        timezone="Asia/Tokyo",
        business_day_cutoff=time(5, 0),
        fetched_at=datetime(2026, 10, 25, 20, 34, tzinfo=UTC),
    )
    result = MachineDetailParser().parse((D / "machine_detail_0277.html").read_bytes(), ctx)
    assert result.issues == []
    assert result.source_reported_at == datetime(2026, 10, 25, 22, 14, tzinfo=ZoneInfo("Asia/Tokyo"))
    by_day = {o.business_date: o for o in result.observations}
    assert sorted(by_day) == [date(2026, 10, d) for d in range(19, 26)]

    today = by_day[date(2026, 10, 25)]
    assert today.source_machine_number == "0277" and today.source_model_name == "マイジャグラーⅥ"
    values = {k: f.value for k, f in today.fields.items()}
    assert values == {
        "bb_count": 20,
        "rb_count": 19,
        "total_games": 7506,
        "last_games": 427,
        "net_medals": -1981,
        "bonus_count": 39,
    }
    assert today.fields["net_medals"].raw == "▲ 1981"
    assert today.fields["bonus_count"].origin == "calculated"
    assert today.extra["total_prob"] == "1/192.5" and today.extra["big_max"] == "36"
    assert by_day[date(2026, 10, 24)].fields["net_medals"].value == 1698

    # 特賞履歴は本日の観測にだけ付く。最新 20 件（39〜20 回目）なので partial
    series = today.bonus_series
    assert series.status == "partial" and len(series.events) == 20
    latest = max(series.events, key=lambda e: e.ordinal)
    assert latest.ordinal == 39 and latest.hit_games == 587 and latest.payout_medals == 240
    assert latest.normalized_type == "BB"
    assert latest.event_time == datetime(2026, 10, 25, 20, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
    assert by_day[date(2026, 10, 24)].bonus_series is None


def test_machine_detail_without_data_is_an_error():
    # データが届く前の画面（表の枠だけ）は観測を作らない
    html = (D / "machine_detail_error451.html").read_text(encoding="utf-8").replace("error(451)", "")
    result = MachineDetailParser().parse(html.encode(), CTX)
    assert result.observations == [] and result.issues[0].code == "NO_DETAIL_DATA"


def test_error_page_detected():
    html = _read("machine_detail_error451.html")
    assert page_error_code(html) == 451
    assert page_error_code(_read("model_page_myjuggler6.html")) is None
    result = MachineDetailParser().parse(html.encode(), CTX)
    assert result.observations == [] and result.issues[0].code == "PAGE_ERROR"


def test_all_adapter_parsers_are_registered_for_reparse():
    """原本に記録されるパーサー名は、reparse で引けるよう登録簿にすべて載っていること。"""
    from slot_data.adapters.manual import parser_for_file
    from slot_data.adapters.pscube import PsCubeAdapter
    from slot_data.config import SourceConfig
    from slot_data.ingestion.parser_registry import get_parser

    adapter = PsCubeAdapter(SourceConfig(code="pscube", name="p"))
    parsers = [
        adapter.parser_for(kind, "text/html") for kind in ("model_list", "model_page", "machine_detail")
    ]
    parsers += [parser_for_file("a.json")[0], parser_for_file("a.csv")[0]]
    for parser in parsers:
        registered = get_parser(parser.name)
        assert type(registered) is type(parser) and registered.version == parser.version
