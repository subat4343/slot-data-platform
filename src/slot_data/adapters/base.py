"""サイト別アダプターの共通インターフェース。

段階: fetch -> raw persist -> parse -> validate -> normalize -> reconcile -> publish。
アダプターが担うのは fetch（取得計画と取得）と parse（原本 -> ParsedObservation）だけで、
以降の段階はサイト非依存のパイプラインが行う。
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any, Protocol

from slot_data.config import AccessPolicy, CollectTarget, SourceConfig
from slot_data.schemas.parsed import ParseResult


class AccessProhibitedError(RuntimeError):
    """利用条件により自動取得できない。収集を止め、手動インポートを使う。"""


class SourceNotImplementedError(NotImplementedError):
    """取得仕様が未確認のため実装していない。"""


@dataclass(frozen=True)
class FetchRequest:
    kind: str  # 例: 'hall_daily', 'machine_detail'
    url: str
    method: str = "GET"
    params: tuple[tuple[str, str], ...] = ()
    hall_code: str | None = None
    business_date: date | None = None
    machine_number: str | None = None

    def fingerprint(self) -> str:
        """取得要求の同一性キー（途中再開・冪等性に使う）。秘密情報を含めないこと。"""
        payload = json.dumps(
            {
                "kind": self.kind,
                "method": self.method.upper(),
                "url": self.url,
                "params": sorted(self.params),
                "hall": self.hall_code,
                "date": self.business_date.isoformat() if self.business_date else None,
                "machine": self.machine_number,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class FetchResult:
    content: bytes
    content_type: str
    fetched_at: datetime
    resource_locator: str | None = None
    status_code: int | None = None
    source_reported_at: datetime | None = None
    response_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParseContext:
    source_code: str
    hall_code: str
    timezone: str
    business_day_cutoff: time | None
    fetched_at: datetime
    resource_locator: str | None = None
    business_date: date | None = None
    response_metadata: dict[str, Any] = field(default_factory=dict)


class Parser(Protocol):
    name: str
    version: str

    def parse(self, data: bytes, ctx: ParseContext) -> ParseResult: ...


class SourceAdapter(ABC):
    source_code: str
    adapter_version: str

    def __init__(self, config: SourceConfig) -> None:
        self.config = config

    def ensure_automated_fetch_allowed(self) -> None:
        ensure_allowed(self.config.code, self.config.access_policy)

    @abstractmethod
    def plan(self, target: CollectTarget, business_dates: list[date]) -> list[FetchRequest]: ...

    @abstractmethod
    def fetch(self, request: FetchRequest, client: Any) -> FetchResult: ...

    @abstractmethod
    def parser_for(self, request_kind: str, content_type: str) -> Parser: ...


def ensure_allowed(source_code: str, policy: AccessPolicy) -> None:
    if policy.automated_fetch == "permitted" and policy.permission_reference:
        return
    if policy.automated_fetch == "permitted":
        raise AccessProhibitedError(
            f"{source_code}: automated_fetch=permitted には permission_reference（許可の根拠）が必要です"
        )
    reason = "利用条件で禁止" if policy.automated_fetch == "prohibited" else "利用条件が未確認"
    evidence = "; ".join(policy.evidence) if policy.evidence else "（記録なし）"
    raise AccessProhibitedError(
        f"{source_code}: 自動取得は実行できません（{reason}）。根拠: {evidence}。"
        " 手動インポート（slot-data import-observations）を利用してください。"
    )
