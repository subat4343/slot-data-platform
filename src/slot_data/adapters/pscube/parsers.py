"""P's CUBE（DMMぱちタウン埋め込み「大当り情報」）の画面 HTML のパーサー。

2026-10-23 に実画面で確認した構造に基づく（tests/fixtures/pscube/）。
- 機種一覧（nc-v03-001.php）: ``ul#ulKI li a.btn-ki`` に機種名（.nc-label）・レート（.nc-upper）・台数（.nc-lower）
- 機種画面（nc-v05-003.php）: 台番号（a.btn-dai）× 日別列（td.column、見出し「本日」「N日前」）の BONUS 回数、
  機種名（.nc-bar-title .nc-shindai）、更新時刻（.nc-upYMDhms）
- 台詳細（nc-v06-001.php）: 拒否されると ``span.notifyMessage`` に ``error(451)`` が出る。
  正常表示時の構造は未確認のため、台詳細は原本保存のみ行い、パーサーは構造確認後に追加する（reparse で取り込む）。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from slot_data.adapters.base import ParseContext
from slot_data.domain.enums import FieldStatus, Severity, ValueOrigin
from slot_data.domain.timeutil import business_date_of
from slot_data.schemas.parsed import ParsedField, ParsedObservation, ParseIssue, ParseResult

_DAY_HEADER = re.compile(r"^(本日|(\d+)日前)")
_UPDATED = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})")
_ERROR = re.compile(r"error\((\d+)\)")


@dataclass(frozen=True)
class ModelEntry:
    name: str
    href: str
    rate_label: str | None
    machine_count: int | None
    bai: str | None
    style_class: str | None  # btn-ki-0/1/2（新台表示などの見た目の区別。意味は未確認なので原文で保持）


def _text(node) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node is not None else ""


def _int(text: str) -> int | None:
    t = unicodedata.normalize("NFKC", text).replace(",", "").strip()
    t = t.replace("▲", "-")
    return int(t) if re.fullmatch(r"-?\d+", t) else None


def parse_model_list(html: str) -> list[ModelEntry]:
    soup = BeautifulSoup(html, "html.parser")
    entries = []
    for a in soup.select("ul#ulKI li a.btn-ki"):
        href = a.get("href", "")
        count_text = _text(a.select_one(".nc-lower"))
        classes = [c for c in a.get("class", []) if c.startswith("btn-ki-")]
        entries.append(
            ModelEntry(
                name=_text(a.select_one(".nc-label")),
                href=href,
                rate_label=_text(a.select_one(".nc-upper")) or None,
                machine_count=_int(count_text.replace("台", "")) if count_text else None,
                bai=parse_qs(urlsplit(href).query).get("bai", [None])[0],
                style_class=classes[0] if classes else None,
            )
        )
    return entries


def parse_machine_links(html: str) -> list[tuple[str, str]]:
    """機種画面の台番号リンク（台番号, href）。"""
    soup = BeautifulSoup(html, "html.parser")
    return [(_text(a), a.get("href", "")) for a in soup.select("a.btn-dai")]


def page_error_code(html: str) -> int | None:
    """画面上部のお知らせ欄に出るエラー番号（例: 451）。"""
    soup = BeautifulSoup(html, "html.parser")
    msg = soup.select_one("span.notifyMessage")
    m = _ERROR.search(_text(msg)) if msg is not None else None
    return int(m.group(1)) if m else None


def parse_updated_at(html: str, tz: str) -> datetime | None:
    soup = BeautifulSoup(html, "html.parser")
    m = _UPDATED.search(_text(soup.select_one(".nc-upYMDhms")))
    if not m:
        return None
    y, mo, d, h, mi = (int(x) for x in m.groups())
    return datetime(y, mo, d, h, mi, tzinfo=ZoneInfo(tz))


class ModelListParser:
    """機種一覧は巡回の目次で、日次の観測値は含まない。原本として保存し、観測は作らない。"""

    name = "pscube_model_list"
    version = "1.0.0"

    def parse(self, data: bytes, ctx: ParseContext) -> ParseResult:
        html = data.decode("utf-8")
        result = ParseResult()
        if not parse_model_list(html):
            result.issues.append(
                ParseIssue(code="NO_MODELS", severity=Severity.WARNING, message="機種一覧に機種がありません")
            )
        return result


class ModelPageParser:
    """機種画面: 台番号ごとの日別 BONUS 回数（本日〜6日前）。

    - 「本日」は画面の更新時刻（無ければ取得時刻）が属する営業日。「N日前」はその N 日前
    - 空欄は、その日にその台のデータが無い（未設置など）とみなし観測を作らない
    - BONUS は BIG/REG を区別しない合計として bonus_count に入れる（表示ラベルは原文で保持）
    """

    name = "pscube_model_page"
    version = "1.0.0"

    def parse(self, data: bytes, ctx: ParseContext) -> ParseResult:
        html = data.decode("utf-8")
        soup = BeautifulSoup(html, "html.parser")
        result = ParseResult()
        code = page_error_code(html)
        if code is not None:
            result.issues.append(
                ParseIssue(code="PAGE_ERROR", severity=Severity.ERROR, message=f"page shows error({code})")
            )
            return result

        model_name = _text(soup.select_one(".nc-bar-title .nc-shindai")) or _text(
            soup.select_one(".nc-bar-title")
        )
        updated = parse_updated_at(html, ctx.timezone)
        result.source_reported_at = updated
        base_day = business_date_of(updated or ctx.fetched_at, ctx.timezone, ctx.business_day_cutoff)
        machines = [num for num, _ in parse_machine_links(html)]
        if not machines:
            result.issues.append(
                ParseIssue(code="NO_MACHINES", severity=Severity.ERROR, message="台番号が見つかりません")
            )
            return result

        bai = parse_qs(urlsplit(ctx.resource_locator or "").query).get("bai", [None])[0]
        is_new = soup.select_one(".nc-bar-title .nc-shindai") is not None
        by_machine: dict[str, dict[date, tuple[int, str, str]]] = {m: {} for m in machines}
        for column in soup.select("#tblDAbv2 td.column"):
            cells = column.select(":scope > div.outer")
            if not cells:
                continue
            header = _text(cells[0])
            m = _DAY_HEADER.match(header)
            if not m:
                result.issues.append(
                    ParseIssue(code="UNKNOWN_COLUMN", severity=Severity.WARNING, message=header[:40])
                )
                continue
            day = base_day - timedelta(days=int(m.group(2) or 0))
            label = header.replace(m.group(1), "").strip() or "BONUS"
            values = cells[1:]
            if len(values) != len(machines):
                result.issues.append(
                    ParseIssue(
                        code="ROW_MISMATCH",
                        severity=Severity.ERROR,
                        message=f"{header}: {len(values)} values for {len(machines)} machines",
                    )
                )
                continue
            for machine, cell in zip(machines, values, strict=True):
                raw = _text(cell)
                if raw == "":
                    continue
                value = _int(raw)
                if value is None:
                    result.issues.append(
                        ParseIssue(
                            code="UNPARSEABLE",
                            severity=Severity.WARNING,
                            message=f"{machine} {header}: {raw!r}",
                        )
                    )
                by_machine[machine][day] = (value, raw, label)

        for machine, days in by_machine.items():
            for day, (value, raw, label) in sorted(days.items()):
                field = (
                    ParsedField(
                        value=value,
                        status=FieldStatus.OBSERVED,
                        raw=raw,
                        label=label,
                        category="BONUS",
                        unit="回",
                        origin=ValueOrigin.DISPLAYED,
                    )
                    if value is not None
                    else ParsedField(status=FieldStatus.UNPARSEABLE, raw=raw, label=label)
                )
                result.observations.append(
                    ParsedObservation(
                        source_machine_number=machine,
                        observed_at=ctx.fetched_at,
                        business_date=day,
                        source_reported_at=updated,
                        source_model_name=model_name or None,
                        fields={"bonus_count": field},
                        scope="P's CUBE 機種画面（日別 BONUS 回数）",
                        extra={"bai": bai, "is_new_model": is_new, "page": "model"},
                    )
                )
        return result


class MachineDetailParser:
    """台詳細。正常表示時の構造が未確認のため、現時点では観測を作らない（原本は保存される）。

    構造を確認したら、このクラスを実装してバージョンを上げ、``slot-data reparse`` で保存済み原本から取り込む。
    """

    name = "pscube_machine_detail"
    version = "0.1.0"

    def parse(self, data: bytes, ctx: ParseContext) -> ParseResult:
        html = data.decode("utf-8")
        result = ParseResult()
        code = page_error_code(html)
        if code is not None:
            result.issues.append(
                ParseIssue(code="PAGE_ERROR", severity=Severity.ERROR, message=f"page shows error({code})")
            )
            return result
        result.issues.append(
            ParseIssue(
                code="DETAIL_PARSER_PENDING",
                severity=Severity.WARNING,
                message="台詳細の構造が未確認のため観測は未作成（原本保存済み）",
            )
        )
        return result
