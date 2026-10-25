"""P's CUBE（DMMぱちタウン埋め込み「大当り情報」）の画面 HTML のパーサー。

2026-10-23 に実画面で確認した構造に基づく（tests/fixtures/pscube/）。
- 機種一覧（nc-v03-001.php）: ``ul#ulKI li a.btn-ki`` に機種名（.nc-label）・レート（.nc-upper）・台数（.nc-lower）
- 機種画面（nc-v05-003.php）: 台番号（a.btn-dai）× 日別列（td.column、見出し「本日」「N日前」）の BONUS 回数、
  機種名（.nc-bar-title .nc-shindai）、更新時刻（.nc-upYMDhms）
- 台詳細（nc-v06-001.php）: 日別表（#tblDAbv2、行見出し + td.column）と本日の特賞履歴（#tblHISTb）。
  拒否されると ``span.notifyMessage`` に ``error(451)`` が出る（2026-10-26 に正常表示の構造を確認）。
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
from slot_data.schemas.parsed import (
    ParsedBonusEvent,
    ParsedBonusSeries,
    ParsedField,
    ParsedObservation,
    ParseIssue,
    ParseResult,
)

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
    """台詳細: 日別（本日〜6日前）の BIG/REG/累計ゲーム/最終ゲーム/出枚数と、本日の特賞履歴。

    2026-10-26 に保存した実画面（tests/fixtures/pscube/machine_detail_0277.html）に基づく。
    - 「本日」は画面の更新時刻が属する営業日（深夜に開いても前日分として扱う）
    - 出枚数は「▲」がマイナス。差枚（net_medals）として保存し、表示ラベルと定義を残す
    - bonus_count は BIG + REG の計算値（機種画面の BONUS と同じ定義）
    - 確率・BIG過去最高は日次指標ではないため、原文を extra に残す
    - 特賞履歴は表示されている分だけ（初期表示は最新 20 件）。1 回目まで無ければ partial
    """

    name = "pscube_machine_detail"
    version = "1.0.0"

    ROW_METRICS = {
        "BIG": ("bb_count", "BB"),
        "REG": ("rb_count", "RB"),
        "累計ゲーム": ("total_games", None),
        "最終ゲーム": ("last_games", None),
        "出枚数": ("net_medals", None),
    }
    ROW_EXTRA = {
        "BIG確率": "big_prob",
        "REG確率": "reg_prob",
        "合成確率": "total_prob",
        "BIG過去最高": "big_max",
    }
    HISTORY_TYPES = {"BIG": "BB", "REG": "RB"}

    def parse(self, data: bytes, ctx: ParseContext) -> ParseResult:
        html = data.decode("utf-8")
        result = ParseResult()
        code = page_error_code(html)
        if code is not None:
            result.issues.append(
                ParseIssue(code="PAGE_ERROR", severity=Severity.ERROR, message=f"page shows error({code})")
            )
            return result
        soup = BeautifulSoup(html, "html.parser")
        labels = self._row_labels(soup)
        if "累計ゲーム" not in labels:
            result.issues.append(
                ParseIssue(
                    code="NO_DETAIL_DATA",
                    severity=Severity.ERROR,
                    message="台詳細のデータ表がありません（データ未表示）",
                )
            )
            return result
        machine = self._machine_number(soup)
        if machine is None:
            result.issues.append(
                ParseIssue(code="NO_MACHINE", severity=Severity.ERROR, message="台番号が見つかりません")
            )
            return result
        updated = parse_updated_at(html, ctx.timezone)
        result.source_reported_at = updated
        base_day = business_date_of(updated or ctx.fetched_at, ctx.timezone, ctx.business_day_cutoff)
        model_name = _text(soup.select_one(".nc-bar-title .nc-shindai")) or _text(
            soup.select_one(".nc-bar-title")
        )

        history = self._history(soup, base_day, ctx, result)
        for column in soup.select("#tblDAbv2 td.column"):
            cells = [_text(c) for c in column.select(":scope > div.outer")]
            if not cells:
                continue
            m = _DAY_HEADER.match(cells[0])
            if not m:
                result.issues.append(
                    ParseIssue(code="UNKNOWN_COLUMN", severity=Severity.WARNING, message=cells[0][:40])
                )
                continue
            values = cells[1:]
            if len(values) != len(labels):
                result.issues.append(
                    ParseIssue(
                        code="ROW_MISMATCH",
                        severity=Severity.ERROR,
                        message=f"{cells[0]}: {len(values)} values for {len(labels)} rows",
                    )
                )
                continue
            if all(v == "" for v in values):
                continue  # その日のデータが無い（未設置など）
            day = base_day - timedelta(days=int(m.group(2) or 0))
            fields: dict[str, ParsedField] = {}
            extra: dict[str, object] = {"page": "machine_detail"}
            for label, raw in zip(labels, values, strict=True):
                if label in self.ROW_METRICS:
                    metric, category = self.ROW_METRICS[label]
                    fields[metric] = self._field(label, raw, category)
                elif label in self.ROW_EXTRA:
                    extra[self.ROW_EXTRA[label]] = raw or None
                else:
                    extra.setdefault("unknown_rows", {})[label] = raw  # type: ignore[union-attr]
            bb, rb = fields.get("bb_count"), fields.get("rb_count")
            if bb and rb and bb.value is not None and rb.value is not None:
                fields["bonus_count"] = ParsedField(
                    value=bb.value + rb.value,
                    status=FieldStatus.OBSERVED,
                    label="BIG+REG",
                    category="BONUS",
                    unit="回",
                    origin=ValueOrigin.CALCULATED,
                    definition="BIG と REG の合計（機種画面の BONUS と同じ）",
                )
            result.observations.append(
                ParsedObservation(
                    source_machine_number=machine,
                    observed_at=ctx.fetched_at,
                    business_date=day,
                    source_reported_at=updated,
                    source_model_name=model_name or None,
                    fields=fields,
                    scope="P's CUBE 台詳細（日別）",
                    bonus_series=history if day == base_day else None,
                    extra=extra,
                )
            )
        return result

    @staticmethod
    def _machine_number(soup: BeautifulSoup) -> str | None:
        m = re.search(r"台番号\s*([0-9A-Za-z-]+)", _text(soup.body) if soup.body else "")
        if m:
            return m.group(1)
        for cls in soup.body.get("class", []) if soup.body else []:
            if cls.startswith("nc-da-"):
                return cls.removeprefix("nc-da-")
        return None

    @staticmethod
    def _row_labels(soup: BeautifulSoup) -> list[str]:
        """日別表の行見出し（先頭の空セルを除く）。"""
        header = soup.select_one("#tblDAbv2 td.nc-grid-color-fix")
        if header is None:
            # 行見出しの列は td.column より前にある最初の列
            first = soup.select_one("#tblDAbv2 td")
            header = first
        if header is None:
            return []
        cells = [_text(c) for c in header.select(":scope > div.outer")]
        return cells[1:] if cells and cells[0] == "" else cells

    def _field(self, label: str, raw: str, category: str | None) -> ParsedField:
        if raw == "":
            return ParsedField(status=FieldStatus.NOT_PUBLISHED, label=label)
        value = _int(raw.replace(" ", ""))
        if value is None:
            return ParsedField(status=FieldStatus.UNPARSEABLE, raw=raw, label=label)
        definition = "出枚数（▲ はマイナス）" if label == "出枚数" else None
        return ParsedField(
            value=value,
            status=FieldStatus.OBSERVED,
            raw=raw,
            label=label,
            category=category,
            origin=ValueOrigin.DISPLAYED,
            definition=definition,
        )

    def _history(
        self, soup: BeautifulSoup, base_day: date, ctx: ParseContext, result: ParseResult
    ) -> ParsedBonusSeries | None:
        table = soup.select_one("#tblHISTb")
        if table is None:
            return None
        tz = ZoneInfo(ctx.timezone)
        cutoff = ctx.business_day_cutoff
        events: list[ParsedBonusEvent] = []
        for row in table.select(":scope > tr"):
            cells = [_text(td) for td in row.find_all("td")]
            if len(cells) < 5:
                continue
            ordinal, clock, games, payout, status = cells[:5]
            n = _int(ordinal)
            tm = re.fullmatch(r"(\d{1,2}):(\d{2})", clock)
            if n is None or n < 1:
                result.issues.append(
                    ParseIssue(code="BAD_HISTORY_ROW", severity=Severity.WARNING, message=str(cells))
                )
                continue
            event_time = None
            if tm:
                hour, minute = int(tm.group(1)), int(tm.group(2))
                day = base_day
                # 営業日境界より前の時刻（深夜）は翌日の日付
                if cutoff is not None and (hour, minute) < (cutoff.hour, cutoff.minute):
                    day = base_day + timedelta(days=1)
                event_time = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
            events.append(
                ParsedBonusEvent(
                    ordinal=n,
                    event_time=event_time,
                    hit_games=_int(games),
                    raw_label=status,
                    normalized_type=self.HISTORY_TYPES.get(status),
                    payout_medals=_int(payout),
                    raw_attributes={"回数": ordinal, "時刻": clock, "ゲーム": games, "出枚pt": payout},
                )
            )
        if not events:
            return ParsedBonusSeries(status="empty", events=[])
        complete = min(e.ordinal for e in events) == 1
        return ParsedBonusSeries(status="complete" if complete else "partial", events=events)
