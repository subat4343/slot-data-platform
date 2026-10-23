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


def test_error_page_detected():
    html = _read("machine_detail_error451.html")
    assert page_error_code(html) == 451
    assert page_error_code(_read("model_page_myjuggler6.html")) is None
    result = MachineDetailParser().parse(html.encode(), CTX)
    assert result.observations == [] and result.issues[0].code == "PAGE_ERROR"
