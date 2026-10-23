"""台データオンライン アダプター。

2026-10-21 時点の調査（docs/source_contracts.md）:
- robots.txt が全 User-agent に対し ``/9*``, ``/*/1*``, ``/*/2*``, ``/*/9*``（台・店舗の詳細系パス）を Disallow
- 利用規約 第4条10号: データベース等の複製を禁止、第4条11号・第13条2項: 取得情報の第三者提供を禁止
自動取得はこれらに抵触するため実装していない。運営者の許可（データ提供契約等）を得た場合のみ、
許可された方式に合わせて実装する。それまでは手動インポートを使う。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from slot_data.adapters.base import (
    FetchRequest,
    FetchResult,
    Parser,
    SourceAdapter,
    SourceNotImplementedError,
)
from slot_data.config import CollectTarget


class DaidataOnlineAdapter(SourceAdapter):
    source_code = "daidata_online"
    adapter_version = "0.1.0-gated"

    def plan(self, target: CollectTarget, business_dates: list[date]) -> list[FetchRequest]:
        self.ensure_automated_fetch_allowed()
        raise SourceNotImplementedError(
            "台データオンライン: 許可された取得方式が未確定のため未実装です（docs/source_contracts.md）"
        )

    def fetch(self, request: FetchRequest, client: Any) -> FetchResult:
        self.ensure_automated_fetch_allowed()
        raise SourceNotImplementedError("台データオンライン: fetch は未実装です")

    def parser_for(self, request_kind: str, content_type: str) -> Parser:
        raise SourceNotImplementedError(
            "台データオンライン: サイト形式のパーサーは未実装です。手動インポート形式を使ってください"
        )
